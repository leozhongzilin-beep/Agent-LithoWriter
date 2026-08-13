"""CSL citation rendering (PRD KB-Completion group D).

Wraps citeproc-py: style resolution from the `citation_styles` table or the
bundled csl/ directory, in-text and full-bibliography rendering from the
canonical bibliographic record. citeproc-py is a runtime dependency only when
CSL rendering is actually used — `StyleUnavailable` lets the citation layer
fall back to the minimal generated form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import KBStore

_STYLE_DIR = Path(__file__).resolve().parent / "csl" / "styles"
_style_cache: dict[str, Any] = {}


class StyleUnavailable(Exception):
    """Raised when a CSL style cannot be resolved or parsed."""


def bundled_style_ids() -> list[str]:
    return sorted(p.stem for p in _STYLE_DIR.glob("*.csl"))


def resolve_style_path(store: KBStore, style_id: str) -> Path | None:
    """style resolution: citation_styles table row -> bundled .csl file."""
    row = store.conn.execute(
        "SELECT csl_path FROM citation_styles WHERE style_id = ?", (style_id,)
    ).fetchone()
    if row is not None and row["csl_path"] and Path(row["csl_path"]).exists():
        return Path(row["csl_path"])
    bundled = _STYLE_DIR / f"{style_id}.csl"
    return bundled if bundled.exists() else None


def seed_styles(store: KBStore) -> dict[str, int]:
    """Register bundled styles in the citation_styles table (idempotent)."""
    count = 0
    for style_id in bundled_style_ids():
        store.conn.execute(
            "INSERT INTO citation_styles (style_id, name, csl_path, version, description) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(style_id) DO UPDATE SET "
            "name=excluded.name, csl_path=excluded.csl_path",
            (style_id, style_id.replace("-", " ").title(),
             str(_STYLE_DIR / f"{style_id}.csl"), "1.0", "bundled"),
        )
        count += 1
    store.conn.commit()
    return {"citation_styles": count}


def load_style(store: KBStore, style_id: str) -> Any:
    """Load a cached citeproc-py style; raises StyleUnavailable otherwise."""
    if style_id in _style_cache:
        return _style_cache[style_id]
    path = resolve_style_path(store, style_id)
    if path is None:
        raise StyleUnavailable(f"no CSL style for {style_id!r}")
    try:
        from citeproc import CitationStylesStyle
    except ImportError as exc:
        raise StyleUnavailable(
            "citeproc-py is not installed; `pip install citeproc-py` to enable "
            "CSL rendering"
        ) from exc
    try:
        style = CitationStylesStyle(str(path), validate=False)
    except Exception as exc:
        raise StyleUnavailable(f"invalid CSL style {style_id!r}: {exc}") from exc
    _style_cache[style_id] = style
    return style


# ---------------------------------------------------------------------------
# record -> CSL JSON
# ---------------------------------------------------------------------------

def item_json(paper: dict[str, Any]) -> dict[str, Any] | None:
    """Map a paper's canonical bibliographic record to a CSL JSON item."""
    br = paper.get("bibliographic_record") or {}
    title = br.get("title") or paper.get("title")
    if not title:
        return None
    item: dict[str, Any] = {
        "id": paper.get("citation_key") or paper.get("paper_id"),
        "type": _type_map(paper.get("article_type")),
        "title": title,
        "author": [_author_json(a) for a in (br.get("authors") or [])],
    }
    year = paper.get("year")
    if year:
        item["issued"] = {"date-parts": [[int(year)]]}
    for csl_key, rec_key in (
        ("container-title", "container_title"),
        ("volume", "volume"),
        ("issue", "issue"),
        ("page", "pages"),
        ("publisher", "publisher"),
    ):
        if br.get(rec_key):
            item[csl_key] = str(br[rec_key])
    if paper.get("doi"):
        item["DOI"] = paper["doi"]
    return item


def _author_json(a: Any) -> dict[str, str]:
    if isinstance(a, dict):
        return {"family": a.get("family") or "", "given": a.get("given") or ""}
    text = str(a).strip()
    if "," in text:
        family, _, given = text.partition(",")
        return {"family": family.strip(), "given": given.strip()}
    parts = text.split()
    if len(parts) > 1:
        return {"family": parts[-1], "given": " ".join(parts[:-1])}
    return {"family": text}


def _type_map(article_type: str | None) -> str:
    t = (article_type or "").lower()
    if "conf" in t or "proceeding" in t:
        return "paper-conference"
    if "book" in t:
        return "book"
    return "article-journal"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _new_bibliography(style: Any, items: list[dict[str, Any]]):
    from citeproc import Citation, CitationItem, CitationStylesBibliography
    from citeproc.formatter import plain
    from citeproc.source.json import CiteProcJSON

    bib = CitationStylesBibliography(style, CiteProcJSON(items), formatter=plain)
    citation = Citation([CitationItem(it["id"]) for it in items])
    bib.register(citation)
    return bib, citation


def render_in_text(store: KBStore, paper_id: str, style_id: str) -> str:
    """Rendered in-text citation, e.g. ``(Zhang, 2024)``."""
    style = load_style(store, style_id)
    paper = store.get_paper(paper_id)
    if paper is None:
        return ""
    it = item_json(paper)
    if it is None:
        return ""
    bib, citation = _new_bibliography(style, [it])
    return bib.cite(citation, lambda item: None)


def render_bibliography(store: KBStore, paper_ids: list[str], style_id: str) -> list[str]:
    """Rendered, sorted bibliography entries for a set of papers."""
    style = load_style(store, style_id)
    items = []
    for pid in paper_ids:
        paper = store.get_paper(pid)
        if paper is None:
            continue
        it = item_json(paper)
        if it is not None:
            items.append(it)
    if not items:
        return []
    bib, citation = _new_bibliography(style, items)
    bib.cite(citation, lambda item: None)  # populate cites before bibliography()
    return [str(entry) for entry in bib.bibliography()]
