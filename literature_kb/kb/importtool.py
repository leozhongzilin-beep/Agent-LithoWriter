"""Import pipeline for one canonical package (``kb add``).

Implements the frozen import semantics:

    Upsert + Identity Resolution + Change Detection + Atomic Replacement + Provenance

Resolution ladder (evaluated in order):
    1. same paper_id                       -> upsert that paper
    2. same DOI                            -> resolve to the existing paper
    3. same source_hash, different paper   -> DUPLICATE_SOURCE  (hard block, --force to override)
    4. same title only                     -> POSSIBLE_DUPLICATE (note, proceed)
    5. otherwise                           -> INSERTED, allocate new paper_id

Change detection (only meaningful on UPDATED):
    source_hash unchanged + processor changed -> EXTRACTION_UPDATED (info)
    source_hash changed                       -> SOURCE_CHANGED (warning, allowed)
    source_hash unchanged + processor same    -> NO_CHANGE

All replacement is one SQLite transaction via KBStore.write_package; any
failure rolls back and leaves the previous rows untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import fts, store
from . import package as pkg
from .ids import (
    DEFAULT_DOMAIN,
    dedupe_citation_key,
    format_sub_id,
    hash_file,
    make_citation_key,
)

VALID_GRAPH_RELATIONS = {
    "cites", "extends", "improves", "compares_with", "uses",
    "criticizes", "builds_on", "same_method_family",
}


class ImportBlocked(Exception):
    """Raised when an import must not proceed (e.g. DUPLICATE_SOURCE)."""


@dataclass
class ImportResult:
    paper_id: str
    decision: str                        # INSERTED | UPDATED
    warnings: list[str] = field(default_factory=list)
    matches: list[tuple[str, str]] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    archive_package: Path | None = None
    archive_manifest: Path | None = None
    archived_source: Path | None = None
    fulltext_pointer: str | None = None
    source_hash: str | None = None


def _now(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def import_package(
    kbs: store.KBStore,
    data: dict[str, Any],
    *,
    source_path: str | None = None,
    paper_id_override: str | None = None,
    force: bool = False,
    imported_from: str | None = None,
    now: datetime | None = None,
) -> ImportResult:
    """Validate, resolve, and atomically import one package.

    Raises pkg.PackageError on invalid packages and ImportBlocked when the
    import must stop (DUPLICATE_SOURCE without --force).
    """
    data = pkg.normalize_package(data)
    errors, warnings = pkg.validate_package(data)
    if errors:
        raise pkg.PackageError("package failed validation:\n  - " + "\n  - ".join(errors))

    # ---- identity resolution ----------------------------------------------
    candidate = paper_id_override or pkg.suggested_paper_id(data)
    doi = _get(data, "paper", "L0", "doi")
    title = pkg.title(data)
    year = _as_year(_get(data, "paper", "L0", "year"))

    # source info (hash / reachability / archive target)
    src_info = _resolve_source(kbs, candidate, data, source_path, warnings)

    target, decision, matches, warnings = _resolve_target(
        kbs, candidate, doi, src_info["hash"], title, force, warnings
    )

    # A paper_id match wins the ladder, but its DOI may already belong to a
    # different paper (doi is UNIQUE). Store the paper without that DOI rather
    # than crashing the transaction or silently corrupting identity.
    if any(w.startswith("ID_DOI_CONFLICT") for w in warnings):
        data["paper"]["L0"] = {**data["paper"]["L0"], "doi": None}
        doi = None

    if target is None:
        if candidate:
            # explicit --paper-id (or a suggested id from the package) is
            # honored verbatim; it does not exist yet, so it becomes the new id
            target = candidate
        else:
            if not year:
                raise pkg.PackageError(
                    "cannot auto-assign a paper_id without L0.year; "
                    "pass --paper-id explicitly"
                )
            target = kbs.next_paper_id(_domain_from_tags(data), year)
        decision = "INSERTED"
    else:
        decision = "UPDATED"
        # change detection on existing papers
        old_hash = kbs.get_source_hash(target)
        new_hash = src_info["hash"]
        if old_hash and new_hash:
            if old_hash == new_hash:
                old_proc = _processor_of(kbs, target)
                new_proc = data["processor"]["version"]
                if old_proc != new_proc:
                    warnings.append(
                        f"EXTRACTION_UPDATED: source unchanged, processor "
                        f"{old_proc!r} -> {new_proc!r}"
                    )
            else:
                warnings.append(
                    "SOURCE_CHANGED: source hash differs from previous import "
                    "(re-extraction against a possibly new source)"
                )

    # ---- citation key ------------------------------------------------------
    citation_key = _unique_citation_key(kbs, target, data, warnings)

    # ---- build rows + write atomically -------------------------------------
    rows = _rows_from_package(kbs, data, target, citation_key, src_info, year)
    ts = _now(now)
    job = {
        "paper_id": target,
        "action": "import",
        "decision": decision,
        "warnings": json.dumps(warnings, ensure_ascii=False),
        "source_hash_before": kbs.get_source_hash(target) if decision == "UPDATED" else None,
        "source_hash_after": src_info["hash"],
        "processor_name": data["processor"]["name"],
        "processor_version": data["processor"]["version"],
        "counts": None,  # filled after write
        "imported_from": imported_from,
        "created_at": ts,
    }
    validation = _validation_row(target, errors, warnings, ts)
    counts = kbs.write_package(
        target,
        rows,
        job=job,
        validation=validation,
        post_insert=lambda conn, pid, r: fts.sync_rows(conn, pid, r),
    )
    job["counts"] = json.dumps(counts)
    _patch_job_counts(kbs, target, counts)

    # ---- archive (after successful commit) ----------------------------------
    archived_source = None
    if src_info["copy_from"] is not None:
        archived_source = kbs.archive_source(target, src_info["copy_from"])
        # refresh papers.source_path/source_reachable now that a copy exists
        _update_source_flags(kbs, target, str(archived_source), reachable=True)

    archive_pkg = kbs.write_package_archive(target, data)
    manifest = {
        "paper_id": target,
        "title": title,
        "doi": doi,
        "citation_key": citation_key,
        "source": {
            "path": src_info["path"],
            "hash": src_info["hash"],
            "type": src_info["type"],
            "reachable": src_info["reachable"],
        },
        "processor": {
            "name": data["processor"]["name"],
            "version": data["processor"]["version"],
        },
        "package_spec_version": data["package_spec_version"],
        "decision": decision,
        "imported_at": ts,
        "updated_at": ts,
        "warnings": warnings,
        "row_counts": counts,
    }
    archive_manifest = kbs.write_manifest(target, manifest)

    return ImportResult(
        paper_id=target,
        decision=decision,
        warnings=warnings,
        matches=matches,
        row_counts=counts,
        archive_package=archive_pkg,
        archive_manifest=archive_manifest,
        archived_source=archived_source,
        fulltext_pointer=str(archived_source) if archived_source else src_info["path"],
        source_hash=src_info["hash"],
    )


# ---------------------------------------------------------------------------
# identity resolution ladder
# ---------------------------------------------------------------------------

def _resolve_target(
    kbs: store.KBStore,
    candidate: str | None,
    doi: str | None,
    source_hash: str | None,
    title: str,
    force: bool,
    warnings: list[str],
) -> tuple[str | None, str, list[tuple[str, str]], list[str]]:
    """Return (target_paper_id | None, decision, matches, warnings)."""
    matches: list[tuple[str, str]] = []

    # 1. same paper_id -> upsert
    if candidate and kbs.paper_exists(candidate):
        if doi:
            by_doi = kbs.find_by_doi(doi)
            if by_doi and by_doi != candidate:
                warnings.append(
                    f"ID_DOI_CONFLICT: paper_id {candidate} but DOI {doi} "
                    f"belongs to {by_doi}"
                )
        return candidate, "UPDATED", [("paper_id", candidate)], warnings

    # 2. same DOI -> resolve
    if doi:
        by_doi = kbs.find_by_doi(doi)
        if by_doi:
            if candidate and candidate != by_doi:
                warnings.append(
                    f"REASSIGNED_ID: package paper_id {candidate} resolved to "
                    f"existing {by_doi} via DOI"
                )
            return by_doi, "UPDATED", [("doi", by_doi)], warnings

    # 3. same source_hash, different paper -> DUPLICATE_SOURCE
    if source_hash:
        by_hash = kbs.find_by_source_hash(source_hash)
        if by_hash and by_hash != candidate:
            warnings.append(
                f"DUPLICATE_SOURCE: source already imported as {by_hash}; "
                f"re-adding creates a second paper for one source"
            )
            if not force:
                raise ImportBlocked(
                    f"DUPLICATE_SOURCE: source already imported as {by_hash}. "
                    f"Re-import that paper or use --force to proceed."
                )
            matches.append(("source_hash", by_hash))
            # fall through: create a new paper with --force

    # 4. same title -> POSSIBLE_DUPLICATE (note only)
    if title:
        by_title = kbs.find_by_title(title)
        if by_title:
            warnings.append(
                f"POSSIBLE_DUPLICATE: title matches existing paper(s) {by_title}"
            )
            matches.append(("title", by_title[0]))

    return None, "INSERTED", matches, warnings


# ---------------------------------------------------------------------------
# source handling
# ---------------------------------------------------------------------------

def _resolve_source(
    kbs: store.KBStore,
    candidate: str | None,
    data: dict[str, Any],
    source_path: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    header = data["source"]
    header_hash = header.get("hash")
    header_path = header.get("path")
    src_type = header.get("type")

    copy_from: Path | None = None
    reachable = False
    computed_hash = None

    if source_path:
        p = Path(source_path)
        if p.exists():
            computed_hash = hash_file(p)
            copy_from = p
            reachable = True
            if header_hash and computed_hash != header_hash:
                warnings.append(
                    f"SOURCE_HASH_MISMATCH: --source file hashes to {computed_hash} "
                    f"but package header declares {header_hash}"
                )
        else:
            warnings.append(f"SOURCE_UNREACHABLE: --source path not found: {p}")
    elif header_path and Path(header_path).exists():
        p = Path(header_path)
        computed_hash = hash_file(p)
        copy_from = p
        reachable = True
        if header_hash and computed_hash != header_hash:
            warnings.append(
                f"SOURCE_HASH_MISMATCH: header source.path hashes to {computed_hash} "
                f"but header declares {header_hash}"
            )
    else:
        warnings.append("SOURCE_UNREACHABLE: no usable source file provided")

    return {
        "hash": computed_hash or header_hash,
        "path": str(copy_from) if copy_from else (header_path or None),
        "type": src_type or "unknown",
        "reachable": reachable,
        "copy_from": copy_from,
    }


# ---------------------------------------------------------------------------
# row building
# ---------------------------------------------------------------------------

def _rows_from_package(
    kbs: store.KBStore,
    data: dict[str, Any],
    paper_id: str,
    citation_key: str,
    src_info: dict[str, Any],
    year: int | None,
) -> dict[str, list[dict[str, Any]]]:
    paper = data["paper"]
    L0, L1 = paper["L0"], paper["L1"]
    L2, L3 = paper["L2"], paper["L3"]
    ts = _now(None)

    papers = [{
        "paper_id": paper_id,
        "title": L0.get("title"),
        "one_line_description": L0.get("one_line_description"),
        "authors_summary": L0.get("authors_summary"),
        "year": year,
        "venue": L0.get("venue"),
        "article_type": L0.get("article_type"),
        "doi": L0.get("doi"),
        "url": L0.get("url"),
        "keywords": store._j(L0.get("keywords")),
        "domain_tags": store._j(L0.get("domain_tags")),
        "method_tags": store._j(L0.get("method_tags")),
        "bibliographic_record": store._j(L0.get("bibliographic_record")),
        "citation_key": citation_key,
        "bibtex_key": _bibtex_key_from_cache(L0.get("citation_cache")),
        "citation_cache": store._j(L0.get("citation_cache")),
        "source_hash": src_info["hash"],
        "source_path": src_info["path"],
        "source_type": src_info["type"],
        "source_reachable": 1 if src_info["reachable"] else 0,
        "processor_name": data["processor"]["name"],
        "processor_version": data["processor"]["version"],
        "package_spec_version": data["package_spec_version"],
        "created_at": ts,
        "updated_at": ts,
    }]

    cards = [{
        "paper_id": paper_id,
        "abstract": L1.get("abstract"),
        "research_problem": L1.get("research_problem"),
        "research_gap": L1.get("research_gap"),
        "main_idea": L1.get("main_idea"),
        "method_summary": L1.get("method_summary"),
        "main_contributions": store._j(L1.get("main_contributions")),
        "innovation": L1.get("innovation"),
        "key_findings_summary": L1.get("key_findings_summary"),
        "limitations": store._j(L1.get("limitations")),
        "datasets_summary": L1.get("datasets_summary"),
        "methods_summary": L1.get("methods_summary"),
        "recommended_use": store._j(L1.get("recommended_use")),
    }]

    methods = _method_rows(paper_id, L2)
    metrics = _metric_rows(paper_id, L2)
    comparisons = _comparison_rows(paper_id, L2)
    claims = _claim_rows(paper_id, L3)
    evidence = _evidence_rows(paper_id, L3)
    fulltext = [{
        "paper_id": paper_id,
        "fulltext_pointer": src_info["path"],
        "section_index": store._j((paper.get("L4") or {}).get("section_index")),
        "chunk_available": 0,
        "chunks": None,
    }]
    formulas, variables = _formula_rows(paper_id, data.get("formulas", []))
    citation_records = _citation_record_rows(paper_id, citation_key, data.get("citation_records", []))
    graph = _citation_graph_rows(kbs, paper_id, data.get("citation_graph", []))

    return {
        "papers": papers,
        "paper_cards": cards,
        "paper_methods": methods,
        "paper_metrics": metrics,
        "paper_comparisons": comparisons,
        "paper_claims": claims,
        "paper_evidence": evidence,
        "paper_fulltext": fulltext,
        "formulas": formulas,
        "formula_variables": variables,
        "citation_records": citation_records,
        "citation_graph": graph,
    }


def _method_rows(paper_id: str, L2: dict[str, Any]) -> list[dict[str, Any]]:
    cards = L2.get("methods") or ([L2.get("method_card")] if L2.get("method_card") else [])
    rows = []
    for i, m in enumerate(cards, start=1):
        if not isinstance(m, dict) or not m:
            continue
        rows.append({
            "method_id": format_sub_id(paper_id, "method", i),
            "paper_id": paper_id,
            "method_name": m.get("method_name"),
            "method_family": m.get("method_family"),
            "task": m.get("task"),
            "input": m.get("input"),
            "output": m.get("output"),
            "architecture": m.get("architecture"),
            "algorithm": m.get("algorithm"),
            "optimization": m.get("optimization"),
            "loss_function": m.get("loss_function"),
            "training_strategy": m.get("training_strategy"),
            "inference_strategy": m.get("inference_strategy"),
            "iterative_or_direct": m.get("iterative_or_direct"),
            "system_context": store._j(m.get("system_context")),
        })
    return rows


def _metric_rows(paper_id: str, L2: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, m in enumerate(L2.get("metrics", []), start=1):
        if not isinstance(m, dict):
            continue
        value, value_text = _metric_value(m)
        rows.append({
            "metric_id": format_sub_id(paper_id, "metric", i),
            "paper_id": paper_id,
            "name": m.get("name"),
            "value": value,
            "value_text": value_text,
            "unit": m.get("unit"),
            "status": m.get("status") or "not_reported",
            "agg_type": m.get("agg_type"),
            "condition": store._j(m.get("condition")),
            "baseline": m.get("baseline"),
            "source_evidence_id": m.get("source_evidence_id"),
            "source_page": m.get("source_page"),
            "source_section": m.get("source_section"),
            "confidence": m.get("confidence"),
        })
    return rows


def _metric_value(m: dict[str, Any]) -> tuple[float | None, str | None]:
    v = m.get("value")
    if v is None:
        return None, m.get("value_text")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v), None
    return None, str(v)


def _comparison_rows(paper_id: str, L2: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, c in enumerate(L2.get("comparisons", []), start=1):
        if not isinstance(c, dict):
            continue
        rows.append({
            "comparison_id": format_sub_id(paper_id, "comparison", i),
            "paper_id": paper_id,
            "metric": c.get("metric"),
            "condition": store._j(c.get("condition")),
            "baseline": c.get("baseline"),
            "proposed": c.get("proposed"),
            "improvement": c.get("improvement"),
            "comparison_validity": c.get("comparison_validity") or "not_comparable",
            "source_evidence_id": c.get("source_evidence_id"),
        })
    return rows


def _claim_rows(paper_id: str, L3: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, c in enumerate(L3.get("claims", []), start=1):
        if not isinstance(c, dict):
            continue
        rows.append({
            "claim_id": format_sub_id(paper_id, "claim", i),
            "paper_id": paper_id,
            "claim": c.get("claim"),
            "claim_type": c.get("claim_type"),
            "strength": c.get("strength"),
            "supporting_evidence_ids": store._j(c.get("supporting_evidence_ids")),
            "confidence": c.get("confidence"),
        })
    return rows


def _evidence_rows(paper_id: str, L3: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, e in enumerate(L3.get("evidence", []), start=1):
        if not isinstance(e, dict):
            continue
        rows.append({
            "evidence_id": format_sub_id(paper_id, "evidence", i),
            "paper_id": paper_id,
            "section": e.get("section"),
            "subsection": e.get("subsection"),
            "page": e.get("page"),
            "paragraph_index": e.get("paragraph_index"),
            "figure_ref": e.get("figure_ref"),
            "table_ref": e.get("table_ref"),
            "source_text": e.get("source_text"),
            "claim": e.get("claim"),
            "evidence_type": e.get("evidence_type"),
            "metric_refs": store._j(e.get("metric_refs")),
            "formula_refs": store._j(e.get("formula_refs")),
            "supports_claim_ids": store._j(e.get("supports_claim_ids")),
            "confidence": e.get("confidence"),
        })
    return rows


def _formula_rows(
    paper_id: str, formulas: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frows, vrows = [], []
    for i, f in enumerate(formulas, start=1):
        if not isinstance(f, dict):
            continue
        fid = format_sub_id(paper_id, "formula", i)
        vars_ = [v for v in (f.get("variables") or []) if isinstance(v, dict)]
        frows.append({
            "formula_id": fid,
            "paper_id": paper_id,
            "section": f.get("section"),
            "page": f.get("page"),
            "formula_latex": f.get("formula_latex"),
            "formula_role": f.get("formula_role"),
            "semantic_description": f.get("semantic_description"),
            "variables": store._j([v.get("symbol") for v in vars_] or None),
            "application": f.get("application"),
            "assumptions": f.get("assumptions"),
            "related_formulas": store._j(f.get("related_formulas")),
            "reusability": store._j(f.get("reusability")),
            "notation_dependencies": store._j(f.get("notation_dependencies")),
            "source_evidence_id": f.get("source_evidence_id"),
            "confidence": f.get("confidence"),
        })
        for v in vars_:
            vrows.append({
                "formula_id": fid,
                "symbol": v.get("symbol"),
                "meaning": v.get("meaning") or "unclear",
                "unit": v.get("unit"),
            })
    return frows, vrows


def _citation_record_rows(
    paper_id: str, citation_key: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for r in records:
        if not isinstance(r, dict) or not r.get("style_id"):
            continue
        rows.append({
            "paper_id": paper_id,
            "citation_key": citation_key,
            "style_id": r["style_id"],
            "in_text_citation": r.get("in_text_citation"),
            "bibliography_entry": r.get("bibliography_entry"),
            "reference_number": r.get("reference_number"),
            "style_source": r.get("style_source"),
            "style_version": r.get("style_version"),
        })
    return rows


def _citation_graph_rows(
    kbs: store.KBStore, paper_id: str, edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        target = _resolve_graph_target(kbs, e)
        relation = e.get("relation")
        if target is None or relation not in VALID_GRAPH_RELATIONS:
            continue
        rows.append({
            "source_paper": paper_id,
            "target_paper": target,
            "relation": relation,
            "confidence": e.get("confidence"),
            "source_evidence_id": e.get("evidence_id") or e.get("source_evidence_id"),
        })
    return rows


def _resolve_graph_target(kbs: store.KBStore, edge: dict[str, Any]) -> str | None:
    """Resolve an edge's target to an existing paper_id, if possible.

    Accepts paper_id, citation_key, or title — but only creates edges whose
    target already exists in the KB (FK-safe). Missing targets are skipped and
    reported via kbs.last_skipped_graph (set below).
    """
    pid = edge.get("target_paper")
    if pid and kbs.paper_exists(pid):
        return pid
    ck = edge.get("target_citation_key")
    if ck:
        found = kbs.find_by_citation_key(ck)
        if found:
            return found
    title = edge.get("target_title")
    if title:
        found = kbs.find_by_title(title)
        if found:
            return found[0]
    return None


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _get(data: dict[str, Any], *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _as_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _domain_from_tags(data: dict[str, Any]) -> str:
    tags = _get(data, "paper", "L0", "domain_tags") or []
    if isinstance(tags, list) and tags:
        return str(tags[0]).upper()
    return DEFAULT_DOMAIN


def _bibtex_key_from_cache(cache: dict[str, Any] | None) -> str | None:
    if not isinstance(cache, dict):
        return None
    bib = cache.get("bibtex")
    if not bib:
        return None
    import re
    m = re.search(r"@\w+\{\s*([^,]+),", bib)
    return m.group(1).strip() if m else None


def _unique_citation_key(
    kbs: store.KBStore, paper_id: str, data: dict[str, Any], warnings: list[str]
) -> str:
    L0 = _get(data, "paper", "L0") or {}
    base = L0.get("citation_key")
    if not base:
        first_author = _first_author(L0)
        base = make_citation_key(
            L0.get("title") or "", L0.get("year"), first_author
        )
    existing = kbs.all_citation_keys(exclude=paper_id)
    key = dedupe_citation_key(existing, base)
    if key != base:
        warnings.append(f"CITATION_KEY_DEDUP: {base!r} already taken -> {key!r}")
    return key


def _first_author(L0: dict[str, Any]) -> str:
    br = L0.get("bibliographic_record") or {}
    authors = br.get("authors") or []
    if authors:
        a = authors[0]
        if isinstance(a, str):
            return a
        if isinstance(a, dict):
            given, family = a.get("given", ""), a.get("family", "")
            return f"{family}, {given}".strip(", ") if family else given
    return str(L0.get("authors_summary") or "")


def _processor_of(kbs: store.KBStore, paper_id: str) -> str | None:
    row = kbs.conn.execute(
        "SELECT processor_version FROM papers WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    return row["processor_version"] if row else None


def _validation_row(paper_id: str, errors: list[str], warnings: list[str], ts: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "gates": json.dumps({"QG-1": not errors, "QG-2": not errors,
                             "QG-3": not errors, "QG-4": not errors,
                             "QG-5": not errors}),
        "pass": 1 if not errors else 0,
        "warnings": json.dumps(warnings, ensure_ascii=False),
        "created_at": ts,
    }


def _patch_job_counts(kbs: store.KBStore, paper_id: str, counts: dict[str, int]) -> None:
    """Update the just-inserted processing_jobs row with real counts."""
    kbs.conn.execute(
        "UPDATE processing_jobs SET counts = ? WHERE job_id = "
        "(SELECT MAX(job_id) FROM processing_jobs WHERE paper_id = ?)",
        (json.dumps(counts), paper_id),
    )
    kbs.conn.commit()


def _update_source_flags(
    kbs: store.KBStore, paper_id: str, source_path: str, reachable: bool
) -> None:
    kbs.conn.execute(
        "UPDATE papers SET source_path = ?, source_reachable = ? WHERE paper_id = ?",
        (source_path, 1 if reachable else 0, paper_id),
    )
    kbs.conn.commit()
