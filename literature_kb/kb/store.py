"""KBStore — the SQLite + archive persistence layer for the Literature KB.

Responsibilities:
    - open/init the database (WAL, foreign keys, full schema)
    - paper_id counters (per domain/year, never MAX+1)
    - point reads used by the import resolution ladder
    - atomic write of one paper's rows (single transaction, rollback-safe)
    - JSON package archive + manifest + source copy under raw/<paper_id>/
    - provenance (processing_jobs) and validation_reports as append-only audit

The store is deliberately *storage-only*: it holds no retrieval/search logic
(that is the next milestone) and no identity-resolution policy (that lives in
importtool.import_package).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # Python 3.9-compatible Self (never evaluated at runtime)
    from typing import Self

from . import config as cfg
from . import fts, schema
from .ids import format_paper_id

# Insertion / deletion order respect foreign keys.
_PAPER_TABLES_IN_ORDER = (
    "papers",
    "paper_cards",
    "paper_methods",
    "paper_metrics",
    "paper_comparisons",
    "paper_claims",
    "paper_evidence",
    "paper_fulltext",
    "formulas",
    "formula_variables",
    "citation_records",
    "citation_graph",
)
_DELETE_ORDER = (
    "citation_graph",      # relation-aware (source_paper / target_paper)
    "citation_records",
    "formula_variables",   # relation-aware (via formulas)
    "formulas",
    "paper_evidence",
    "paper_claims",
    "paper_comparisons",
    "paper_metrics",
    "paper_methods",
    "paper_cards",
    "paper_fulltext",
    "embeddings",
    "validation_reports",  # FK to papers — must go before it
    "papers",
)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _j(value: Any) -> str | None:
    """Serialize a Python value to a JSON string (or None)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _jload(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class KBStore:
    """Storage facade over a KB_ROOT."""

    def __init__(self, root: Path, connect: bool = True):
        self.root = Path(root).resolve()
        self.db_path = cfg.db_path(self.root)
        self.conn: sqlite3.Connection | None = None
        if connect:
            self.connect()

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def connect(self) -> KBStore:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self.conn = conn
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> Self:
        if self.conn is None:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------
    def init(self) -> None:
        """Create KB_ROOT subdirectories, schema and FTS index. Idempotent."""
        for sub in cfg.SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        if self.conn is None:
            self.connect()
        schema.create_schema(self.conn)
        fts.create_fts(self.conn)

    @property
    def is_initialized(self) -> bool:
        if self.conn is None:
            return False
        return "papers" in schema.list_tables(self.conn)

    # ------------------------------------------------------------------
    # paper_id counter
    # ------------------------------------------------------------------
    def next_paper_id(self, domain: str, year: int) -> str:
        """Allocate the next paper_id for (domain, year) atomically."""
        domain = (domain or "ILT").upper()
        assert self.conn is not None
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sequences (domain, year, next_value) VALUES (?,?,1)",
                (domain, year),
            )
            row = self.conn.execute(
                "SELECT next_value FROM sequences WHERE domain=? AND year=?",
                (domain, year),
            ).fetchone()
            seq = row["next_value"]
            self.conn.execute(
                "UPDATE sequences SET next_value = next_value + 1 WHERE domain=? AND year=?",
                (domain, year),
            )
        return format_paper_id(domain, year, seq)

    # ------------------------------------------------------------------
    # point reads (used by the import resolution ladder)
    # ------------------------------------------------------------------
    def paper_exists(self, paper_id: str) -> bool:
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT 1 FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        return row is not None

    def find_by_doi(self, doi: str) -> str | None:
        """Resolve a DOI to an existing paper_id, if any."""
        if not doi:
            return None
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT paper_id FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        return row["paper_id"] if row else None

    def find_by_source_hash(self, source_hash: str) -> str | None:
        if not source_hash:
            return None
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT paper_id FROM papers WHERE source_hash = ?", (source_hash,)
        ).fetchone()
        return row["paper_id"] if row else None

    def find_by_citation_key(self, citation_key: str) -> str | None:
        if not citation_key:
            return None
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT paper_id FROM papers WHERE citation_key = ?", (citation_key,)
        ).fetchone()
        return row["paper_id"] if row else None

    def all_citation_keys(self, exclude: str | None = None) -> set:
        """All citation_keys in the KB, optionally excluding one paper."""
        assert self.conn is not None
        sql = "SELECT citation_key FROM papers"
        params: tuple = ()
        if exclude:
            sql += " WHERE paper_id != ?"
            params = (exclude,)
        return {r["citation_key"] for r in self.conn.execute(sql, params).fetchall()}

    def find_by_title(self, title: str) -> list[str]:
        """Titles that match the query under a normalized (lowercased) compare."""
        if not title:
            return []
        assert self.conn is not None
        norm = _normalize_title(title)
        rows = self.conn.execute("SELECT paper_id, title FROM papers").fetchall()
        return [
            r["paper_id"] for r in rows if _normalize_title(r["title"]) == norm
        ]

    # ------------------------------------------------------------------
    # retrieval helpers
    # ------------------------------------------------------------------
    def filter_papers(self, filters: dict[str, Any], limit: int = 200) -> list[str]:
        """paper_ids satisfying metadata filters (year/domain/method/venue)."""
        clauses: list[str] = []
        params: list[Any] = []
        if filters.get("year_from") is not None:
            clauses.append("year >= ?")
            params.append(int(filters["year_from"]))
        if filters.get("year_to") is not None:
            clauses.append("year <= ?")
            params.append(int(filters["year_to"]))
        if filters.get("domain"):
            d = str(filters["domain"]).upper()
            clauses.append("(paper_id LIKE ? OR domain_tags LIKE ?)")
            params.extend([f"{d}_%", f"%{d}%"])
        if filters.get("method"):
            clauses.append("method_tags LIKE ?")
            params.append(f"%{filters['method']}%")
        if filters.get("venue"):
            clauses.append("venue LIKE ?")
            params.append(f"%{filters['venue']}%")
        sql = "SELECT paper_id FROM papers"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY year DESC LIMIT ?"
        params.append(int(limit))
        return [r["paper_id"] for r in self.conn.execute(sql, params).fetchall()]

    def all_paper_ids(self, limit: int = 200) -> list[str]:
        return [r["paper_id"] for r in self.conn.execute(
            "SELECT paper_id FROM papers ORDER BY paper_id LIMIT ?", (int(limit),)
        ).fetchall()]

    def title_index(self) -> list[tuple[str, str]]:
        """(paper_id, title) pairs for every paper, newest first.

        Used for recall-oriented title matching (citation resolution against
        rewritten titles) where the FTS AND-join is too strict.
        """
        return [(r["paper_id"], r["title"] or "") for r in self.conn.execute(
            "SELECT paper_id, title FROM papers ORDER BY year DESC"
        ).fetchall()]

    def like_papers(self, query: str, limit: int = 50) -> list[str]:
        """LIKE fallback over title/description/keywords when FTS is empty."""
        q = f"%{query}%"
        return [r["paper_id"] for r in self.conn.execute(
            "SELECT paper_id FROM papers WHERE title LIKE ? "
            "OR one_line_description LIKE ? OR keywords LIKE ? "
            "ORDER BY year DESC LIMIT ?",
            (q, q, q, int(limit)),
        ).fetchall()]

    def citation_in_degrees(self) -> dict[str, int]:
        """citation_graph in-degree per paper (seminality for ranking)."""
        return {r["target_paper"]: r["c"] for r in self.conn.execute(
            "SELECT target_paper, COUNT(*) AS c FROM citation_graph "
            "GROUP BY target_paper"
        ).fetchall()}

    def evidence_ids_for(self, paper_id: str, limit: int = 3) -> list[str]:
        return [r["evidence_id"] for r in self.conn.execute(
            "SELECT evidence_id FROM paper_evidence WHERE paper_id = ? "
            "ORDER BY evidence_id LIMIT ?",
            (paper_id, int(limit)),
        ).fetchall()]

    def available_levels(self, paper_id: str) -> list[str]:
        levels = ["L0"]
        for level, table in (("L1", "paper_cards"), ("L2", "paper_metrics"),
                             ("L3", "paper_evidence")):
            if self.conn.execute(
                f"SELECT 1 FROM {table} WHERE paper_id = ?", (paper_id,)
            ).fetchone():
                levels.append(level)
        # L4 means *chunks exist*, not merely that a fulltext row was written
        if self.conn.execute(
            "SELECT 1 FROM paper_fulltext WHERE paper_id = ? AND chunk_available = 1",
            (paper_id,),
        ).fetchone():
            levels.append("L4")
        if self.conn.execute(
            "SELECT 1 FROM formulas WHERE paper_id = ?", (paper_id,)
        ).fetchone():
            levels.append("FORMULA")
        return levels

    def metrics_matching(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Metric rows whose name/condition/value_text mention the query."""
        q = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM paper_metrics WHERE name LIKE ? OR condition LIKE ? "
            "OR value_text LIKE ? ORDER BY paper_id LIMIT ?",
            (q, q, q, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def metric_rows_for(self, paper_id: str, metrics: list[str] | None = None) -> list[dict[str, Any]]:
        if metrics:
            placeholders = ", ".join("?" * len(metrics))
            rows = self.conn.execute(
                f"SELECT * FROM paper_metrics WHERE paper_id = ? "
                f"AND name IN ({placeholders}) ORDER BY metric_id",
                [paper_id, *metrics],
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM paper_metrics WHERE paper_id = ? ORDER BY metric_id",
                (paper_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["condition"] = _jload(d.get("condition"))
            out.append(d)
        return out

    def comparison_rows_for(self, paper_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM paper_comparisons WHERE paper_id = ? ORDER BY comparison_id",
            (paper_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["condition"] = _jload(d.get("condition"))
            out.append(d)
        return out

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for col in ("keywords", "domain_tags", "method_tags",
                    "bibliographic_record", "citation_cache"):
            d[col] = _jload(d.get(col))
        return d

    def update_citation_metadata(self, paper_id: str, doi: str, bibtex: str) -> None:
        """Backfill a paper's citation identity (doi + bibtex) in place.

        Used to repair papers imported with a missing/wrong DOI, which leaves
        ``citation_cache`` empty and therefore makes the paper uncitable.
        The BibTeX internal key is derived from ``bibtex`` (never guessed).
        """
        assert self.conn is not None
        m = re.search(r"@\w+\{\s*([^,]+),", bibtex)
        bibtex_key = m.group(1).strip() if m else None
        cache = json.dumps({"bibtex": bibtex}, ensure_ascii=False)
        self.conn.execute(
            "UPDATE papers SET doi = ?, bibtex_key = ?, citation_cache = ?, "
            "updated_at = ? WHERE paper_id = ?",
            (doi, bibtex_key, cache,
             datetime.now(UTC).isoformat(timespec="seconds"), paper_id),
        )
        self.conn.commit()

    def get_source_hash(self, paper_id: str) -> str | None:
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT source_hash FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        return row["source_hash"] if row else None

    def list_papers(self, domain: str | None = None, year: int | None = None) -> list[dict]:
        assert self.conn is not None
        sql = (
            "SELECT paper_id, title, year, venue, citation_key, doi, source_hash "
            "FROM papers"
        )
        clauses, params = [], []
        if domain:
            clauses.append("paper_id LIKE ?")
            params.append(f"{domain.upper()}_%")
        if year is not None:
            clauses.append("year = ?")
            params.append(int(year))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY paper_id"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # atomic write
    # ------------------------------------------------------------------
    def write_package(
        self,
        paper_id: str,
        rows: dict[str, list[dict[str, Any]]],
        *,
        job: dict[str, Any],
        validation: dict[str, Any] | None = None,
        post_insert: Callable[[sqlite3.Connection, str, dict], None] | None = None,
    ) -> dict[str, int]:
        """Write one paper's rows inside a single transaction.

        Existing rows for ``paper_id`` are deleted first (atomic replace); any
        failure rolls the whole transaction back so old data is untouched.
        (The paper_id counter is consumed by the caller via next_paper_id only
        when a new id is auto-assigned — no sequence work happens here.)

        rows:        {table_name: [row_dict, ...]}
        job:         processing_jobs row (audit log)
        validation:  validation_reports row, if any
        post_insert: called inside the same transaction after inserts
                     (e.g. fts.sync_rows) so derived indexes never drift.
        """
        assert self.conn is not None
        counts: dict[str, int] = {}
        with self.conn:
            self._delete_paper_rows(paper_id)
            for table in _PAPER_TABLES_IN_ORDER:
                n = self._insert_rows(table, rows.get(table, []))
                if n:
                    counts[table] = n
            self._insert_rows("processing_jobs", [job])
            if validation:
                self._insert_rows("validation_reports", [validation])
            if post_insert is not None:
                post_insert(self.conn, paper_id, rows)
        return counts

    def _delete_paper_rows(self, paper_id: str) -> None:
        assert self.conn is not None
        # relation-aware deletes for tables without a paper_id column
        self.conn.execute(
            "DELETE FROM formula_variables WHERE formula_id IN "
            "(SELECT formula_id FROM formulas WHERE paper_id = ?)",
            (paper_id,),
        )
        self.conn.execute(
            "DELETE FROM citation_graph WHERE source_paper = ? OR target_paper = ?",
            (paper_id, paper_id),
        )
        for table in _DELETE_ORDER:
            if table in ("formula_variables", "citation_graph"):
                continue
            self.conn.execute(
                f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,)
            )

    def _insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        assert self.conn is not None
        n = 0
        for row in rows:
            if not row:
                continue
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            self.conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            n += 1
        return n

    # ------------------------------------------------------------------
    # archive (files under raw/<paper_id>/)
    # ------------------------------------------------------------------
    def write_package_archive(self, paper_id: str, package: dict[str, Any]) -> Path:
        """Write the canonical latest package as JSON."""
        d = cfg.paper_dir(self.root, paper_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / cfg.ARCHIVE_PACKAGE
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_manifest(self, paper_id: str, manifest: dict[str, Any]) -> Path:
        d = cfg.paper_dir(self.root, paper_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / cfg.ARCHIVE_MANIFEST
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def archive_source(self, paper_id: str, source_path: Path) -> Path:
        """Copy a source document into raw/<paper_id>/source/. Returns the copy."""
        src = Path(source_path)
        target_dir = cfg.source_dir(self.root, paper_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        shutil.copy2(src, target)
        return target

    def source_archived(self, paper_id: str) -> Path | None:
        """Path of the archived source copy for a paper, if it exists."""
        d = cfg.source_dir(self.root, paper_id)
        files = sorted(d.glob("*")) if d.exists() else []
        return files[0] if files else None

    def paper_dir(self, paper_id: str) -> Path:
        return cfg.paper_dir(self.root, paper_id)

    # ------------------------------------------------------------------
    # status / introspection
    # ------------------------------------------------------------------
    def table_counts(self) -> dict[str, int]:
        assert self.conn is not None
        counts: dict[str, int] = {}
        for t in schema.TABLES:
            try:
                n = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
            counts[t] = n
        return counts

    def dangling_sources(self) -> list[str]:
        """paper_ids whose archived source is missing or flagged unreachable."""
        assert self.conn is not None
        rows = self.conn.execute(
            "SELECT paper_id, source_reachable FROM papers WHERE source_reachable = 0"
        ).fetchall()
        out = [r["paper_id"] for r in rows]
        # also: papers that claim a source path but the file vanished
        for r in self.conn.execute(
            "SELECT paper_id, source_path FROM papers WHERE source_path IS NOT NULL"
        ).fetchall():
            if r["source_path"] and not Path(r["source_path"]).exists():
                out.append(r["paper_id"])
        return sorted(set(out))


def _normalize_title(title: str) -> str:
    return "".join(ch for ch in title.lower() if ch.isalnum())
