"""Bibliographic metadata resolution + reconciliation (Skill step 1-2).

Priority (spec §18): Crossref/DOI resolver metadata > structured/PDF metadata
> LLM inference. `resolve_metadata` hits the Crossref API (injectable HTTP for
tests); `merge_metadata` reconciles Crossref + PDF metadata into the L0 fields.
DOIs are never guessed — a DOI is used only when explicitly provided.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CROSSREF_API = "https://api.crossref.org/works"

# Crossref "type" -> our article_type vocabulary
_TYPE_MAP = {
    "journal-article": "journal",
    "proceedings-article": "conf",
    "book-chapter": "book_chapter",
    "book": "book",
    "posted-content": "preprint",
    "preprint": "preprint",
}


@dataclass
class BibRecord:
    title: str = ""
    authors: list[dict[str, str]] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    article_type: str = ""
    doi: str = ""
    url: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""


def _http_get_json(url: str, timeout: int = 20) -> dict[str, Any] | None:
    import requests

    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "paper2kb/0.1 (metadata resolver)"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def crossref_item_to_record(item: dict[str, Any]) -> BibRecord:
    """Map a Crossref /works item to a BibRecord (pure)."""
    authors = []
    for a in item.get("author") or []:
        authors.append({
            "family": a.get("family", ""),
            "given": a.get("given", ""),
        })
    year = None
    issued = item.get("issued", {}).get("date-parts", [[None]])
    if issued and issued[0] and issued[0][0]:
        year = int(issued[0][0])
    container = item.get("container-title") or []
    rec = BibRecord(
        title=(item.get("title") or [""])[0],
        authors=authors,
        year=year,
        venue=container[0] if container else "",
        article_type=_TYPE_MAP.get(item.get("type", ""), "journal"),
        doi=item.get("DOI", ""),
        url=item.get("URL", ""),
        volume=str(item.get("volume", "") or ""),
        issue=str(item.get("issue", "") or ""),
        pages=str(item.get("page", "") or ""),
        publisher=item.get("publisher", ""),
    )
    return rec


def resolve_metadata(
    doi: str | None = None,
    title: str | None = None,
    *,
    http_get: Callable[[str], dict[str, Any] | None] = _http_get_json,
) -> BibRecord | None:
    """Resolve bibliographic metadata via Crossref (DOI first, then title)."""
    if doi:
        data = http_get(f"{CROSSREF_API}/{doi}")
        if data and data.get("message"):
            return crossref_item_to_record(data["message"])
    if title and title.strip():
        import urllib.parse

        q = urllib.parse.quote(title.strip())
        data = http_get(f"{CROSSREF_API}?query.title={q}&rows=1&select=title,author,issued,container-title,type,DOI,URL,volume,issue,page,publisher")
        if data:
            items = data.get("message", {}).get("items", [])
            if items:
                return crossref_item_to_record(items[0])
    return None


def _parse_pdf_author(author: str) -> list[dict[str, str]]:
    """'Li, Ming' -> [{family: Li, given: Ming}]; 'Ming Li' -> reversed."""
    author = (author or "").strip()
    if not author:
        return []
    if "," in author:
        family, _, given = author.partition(",")
        return [{"family": family.strip(), "given": given.strip()}]
    parts = author.split()
    if len(parts) > 1:
        return [{"family": parts[-1], "given": " ".join(parts[:-1])}]
    return [{"family": author}]


def merge_metadata(
    record: BibRecord | None,
    pdf_meta: dict[str, Any] | None,
    *,
    title_override: str | None = None,
) -> dict[str, Any]:
    """Reconcile into L0 fields: Crossref wins, PDF metadata fills gaps."""
    pdf_meta = pdf_meta or {}
    title = (title_override or record.title if record else None
             ) or (title_override or "") or pdf_meta.get("title", "") or ""
    authors = record.authors if record and record.authors else _parse_pdf_author(
        pdf_meta.get("author", ""))
    year = record.year if record and record.year else None
    venue = record.venue if record and record.venue else pdf_meta.get("venue", "") or ""
    doi = record.doi if record and record.doi else ""

    summary = _authors_summary(authors)
    return {
        "title": title,
        "authors": authors,
        "authors_summary": summary,
        "year": year,
        "venue": venue,
        "article_type": record.article_type if record else "journal",
        "doi": doi,
        "url": record.url if record else "",
        "volume": record.volume if record else "",
        "issue": record.issue if record else "",
        "pages": record.pages if record else "",
        "publisher": record.publisher if record else "",
    }


def _authors_summary(authors: list[dict[str, str]]) -> str:
    if not authors:
        return ""
    first = authors[0].get("family", "") or authors[0].get("given", "")
    if len(authors) == 1:
        return first
    return f"{first} et al."
