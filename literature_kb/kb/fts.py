"""FTS5 lexical index (spec §14.2). Wraps the FTS5 virtual-table lifecycle.

Three contentless virtual tables index the write-side rows so `kb add` papers
are immediately searchable with real BM25 (FTS5 confirmed available in this
environment's stdlib sqlite3). Missing/FTS5-malformed queries degrade to empty
or LIKE results — never a crash.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from typing import Any

# table -> (id column(s) marked UNINDEXED, searchable columns)
_FTS_SCHEMA = {
    "fts_papers": {
        "ids": ["paper_id"],
        "cols": ["title", "keywords", "domain_tags", "method_tags",
                 "one_line_description"],
    },
    "fts_evidence": {
        "ids": ["paper_id", "evidence_id"],
        "cols": ["source_text", "claim", "section"],
    },
    "fts_formulas": {
        "ids": ["paper_id", "formula_id"],
        "cols": ["formula_latex", "semantic_description", "variables"],
    },
    "fts_chunks": {
        "ids": ["paper_id", "chunk_id"],
        "cols": ["text", "section"],
    },
}

# source rows dict key -> fts table -> per-row column extractor
_ROW_MAP = {
    "papers": "fts_papers",
    "paper_evidence": "fts_evidence",
    "formulas": "fts_formulas",
}

FTS_TABLES = tuple(_FTS_SCHEMA)


def sync_chunks(
    conn: sqlite3.Connection, paper_id: str, chunks: list[dict[str, Any]]
) -> None:
    """Replace a paper's L4 chunk FTS rows (caller owns the transaction)."""
    conn.execute("DELETE FROM fts_chunks WHERE paper_id = ?", (paper_id,))
    for c in chunks:
        conn.execute(
            "INSERT INTO fts_chunks (paper_id, chunk_id, text, section) "
            "VALUES (?,?,?,?)",
            (paper_id, c.get("chunk_id"), c.get("text") or "",
             c.get("section") or ""),
        )


def create_fts(conn: sqlite3.Connection) -> None:
    """Create the FTS virtual tables if missing (idempotent)."""
    for table, spec in _FTS_SCHEMA.items():
        cols = ", ".join(f"{c} UNINDEXED" for c in spec["ids"]) + (
            ", " if spec["ids"] else ""
        ) + ", ".join(spec["cols"])
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5({cols})")
    conn.commit()


def sync_rows(
    conn: sqlite3.Connection, paper_id: str, rows: dict[str, list[dict[str, Any]]]
) -> None:
    """Replace a paper's FTS rows from the write-side row dict (caller owns txn).

    Called inside the same transaction as `kb add` so the write side and the
    lexical index never drift.
    """
    for source_key, table in _ROW_MAP.items():
        conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))
        for row in rows.get(source_key, []):
            spec = _FTS_SCHEMA[table]
            values = [row.get(c) for c in spec["ids"] + spec["cols"]]
            placeholders = ", ".join("?" * len(values))
            conn.execute(
                f"INSERT INTO {table} ({', '.join(spec['ids'] + spec['cols'])}) "
                f"VALUES ({placeholders})",
                values,
            )


def make_match(query: str, join: str = "AND") -> str:
    """Build a safe FTS5 MATCH expression from free text.

    Each alphanumeric token is quoted (escapes FTS5 operators) and joined with
    `join` (AND for precision, OR for recall e.g. claim verification); ordering
    comes from bm25(). Empty -> '' (returns nothing).
    """
    tokens = re.findall(r"[A-Za-z0-9]+", query or "")
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return ""
    return f" {join} ".join(f'"{t}"' for t in tokens)


def query(
    conn: sqlite3.Connection,
    table: str,
    match: str,
    limit: int = 20,
    extra_where: Sequence[str] = (),
    params: Sequence[Any] = (),
    match_expr: str | None = None,
) -> list[dict[str, Any]]:
    """Run an FTS5 query over one index table, best-first.

    Returns rows dicts with a `score` = -bm25 (higher is better). Malformed or
    empty match -> []. `extra_where`/`params` allow e.g. role filters.
    `match_expr` bypasses make_match() for callers that pre-built a safe
    expression (e.g. an OR-joined verification query).
    """
    expr = match_expr if match_expr is not None else make_match(match)
    if not expr:
        return []
    sql = (
        f"SELECT *, bm25({table}) AS bm FROM {table} "
        f"WHERE {table} MATCH ?"
    )
    clause_params: list[Any] = [expr]
    if extra_where:
        sql += " AND " + " AND ".join(extra_where)
        clause_params.extend(params)
    sql += f" ORDER BY bm LIMIT {int(limit)}"
    try:
        result = conn.execute(sql, clause_params).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in result:
        d = dict(r)
        d["score"] = -float(d.pop("bm"))
        out.append(d)
    return out
