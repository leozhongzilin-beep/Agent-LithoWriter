"""Assemble the canonical KB package (Skill §17 output contract).

Turns the extracted layers + reconciled metadata + parsed doc into the exact
dict shape `kb add` ingests. `paper_id` and `citation_key` are left empty —
they are assigned by the KB's import ladder.
"""

from __future__ import annotations

from typing import Any

PACKAGE_SPEC_VERSION = "1.0"
PROCESSOR_NAME = "paper_to_literature_kb"


def assemble(
    doc,
    meta: dict[str, Any],
    l0: dict[str, Any],
    l1: dict[str, Any],
    l2m: dict[str, Any],
    l2r: dict[str, Any],
    l3: dict[str, Any],
    formulas: dict[str, Any],
    graph: dict[str, Any],
    *,
    source_path: str,
    source_hash: str | None,
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Build the canonical package dict."""
    return {
        "package_spec_version": PACKAGE_SPEC_VERSION,
        "processor": {"name": PROCESSOR_NAME, "version": version},
        "source": {
            "path": source_path,
            "hash": source_hash,
            "type": doc.source_type,
        },
        "paper": {
            "L0": {
                **l0,
                "paper_id": "",
                "citation_key": "",
                "citation_cache": {},
                "bibliographic_record": _bib_record(meta),
            },
            "L1": l1,
            "L2": {
                **l2m,
                "result_card": {},
                "metrics": l2r.get("metrics", []),
                "comparisons": l2r.get("comparisons", []),
            },
            "L3": {
                "claims": l3.get("claims", []),
                "evidence": l3.get("evidence", []),
            },
            "L4": {"fulltext_pointer": source_path},
        },
        "formulas": formulas.get("formulas", []),
        "citation_records": [],
        "citation_graph": graph.get("citation_graph", []),
        "validation_report": {},
    }


def _bib_record(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "authors": meta.get("authors") or [],
        "title": meta.get("title") or "",
        "container_title": meta.get("venue") or "",
        "year": meta.get("year"),
        "volume": meta.get("volume") or "",
        "issue": meta.get("issue") or "",
        "pages": meta.get("pages") or "",
        "publisher": meta.get("publisher") or "",
        "doi": meta.get("doi") or "",
        "url": meta.get("url") or "",
    }
