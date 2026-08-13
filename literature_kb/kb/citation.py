"""Citation resolution (spec §11, §21 resolve_citation).

Three-layer separation preserved: the paper's canonical bibliographic record is
the truth; `citation_records` holds per-style rendered cache entries; CSL
rendering reproduces any style on demand from that record. `resolve_citation`
prefers cache, then CSL, then a minimal in-text citation explicitly marked
`generated: true` — a fallback is never treated as authoritative.
"""

from __future__ import annotations

from typing import Any

from .store import KBStore


def short_citation(paper: dict[str, Any]) -> str:
    """Minimal in-text citation from the canonical bibliographic record."""
    br = paper.get("bibliographic_record") or {}
    authors = br.get("authors") or []
    year = paper.get("year")
    name = ""
    if authors:
        a = authors[0]
        if isinstance(a, dict):
            name = a.get("family") or ""
        elif isinstance(a, str):
            name = a.split()[-1]
    elif paper.get("authors_summary"):
        name = str(paper["authors_summary"]).split()[0]
    if name and year:
        return f"({name} et al., {year})"
    if year:
        return f"({year})"
    return ""


def resolve_citation(
    store: KBStore, paper_id: str, style_id: str | None = None
) -> dict[str, Any] | None:
    """Resolve a paper to a rendered citation for a style.

    Truth order: citation_records cache -> CSL render from the canonical record
    -> minimal (Author, Year) marked `generated: true`.
    """
    paper = store.get_paper(paper_id)
    if paper is None:
        return None

    if style_id:
        row = store.conn.execute(
            "SELECT * FROM citation_records WHERE paper_id = ? AND style_id = ?",
            (paper_id, style_id),
        ).fetchone()
        if row is not None:
            return {
                "paper_id": paper_id,
                "citation_key": row["citation_key"],
                "style_id": style_id,
                "in_text_citation": row["in_text_citation"],
                "bibliography_entry": row["bibliography_entry"],
                "renderer": "cache",
                "generated": False,
            }

        try:
            from .csl import StyleUnavailable, render_bibliography, render_in_text
            in_text = render_in_text(store, paper_id, style_id)
            if in_text:
                entries = render_bibliography(store, [paper_id], style_id)
                return {
                    "paper_id": paper_id,
                    "citation_key": paper.get("citation_key"),
                    "style_id": style_id,
                    "in_text_citation": in_text,
                    "bibliography_entry": entries[0] if entries else None,
                    "renderer": "csl",
                    "generated": False,
                }
        except StyleUnavailable:
            pass  # fall through to the minimal form

    return {
        "paper_id": paper_id,
        "citation_key": paper.get("citation_key"),
        "style_id": style_id,
        "in_text_citation": short_citation(paper),
        "bibliography_entry": None,
        "renderer": "minimal",
        "generated": True,
    }
