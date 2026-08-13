"""Citation verification for the writing agent.

Prevents hallucinated BibTeX. Strategy (from ARIS paper-write):
    Step A: DBLP search -> extract DBLP key -> fetch real .bib
    Step B: CrossRef via DOI (fallback)
    Step C: mark [VERIFY] (last resort, never fabricate)

The LLM asks for citations by *topic hints* (e.g. "transformer attention").
This module turns hints into real verified BibTeX entries. The caller merges
the resulting keys into the draft via a second pass (the LLM references the
keys we provide).
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .config import Config


@dataclass
class VerifiedEntry:
    key: str                       # bibtex key, e.g. vaswani2017attention
    bibtex: str                    # full bibtex entry
    title: str = ""
    year: str = ""
    venue: str = ""
    source: str = ""               # DBLP | CrossRef | LLM | UNVERIFIED
    verified: bool = False


def make_key(title: str, year: str, first_author: str = "") -> str:
    """Generate a consistent bibtex key: {firstauthor}{year}{keyword}.

    Example: "Attention Is All You Need" (2017, Vaswani) -> vaswani2017attention
    """
    def slug(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s).strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"^a |^an |^the ", "", s)
        return s

    author_part = ""
    if first_author:
        # take first author's last name (before comma or after spaces)
        a = first_author.strip().split(",")[0].strip()
        # "Ashish Vaswani" -> "vaswani"; "Vaswani, Ashish" -> "vaswani"
        a = a.split()[-1].lower()
        author_part = re.sub(r"[^a-z]", "", a)

    title_words = slug(title).split()[:4]
    keyword = title_words[0] if title_words else "paper"
    return f"{author_part}{year}{keyword}" or "paper"


class CitationResolver:
    """Verify and resolve citation hints into real BibTeX entries."""

    def __init__(self, config: Config, timeout: int = 20):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "writing-agent/0.1 (academic citation verifier)"})
        self._cache: Dict[str, VerifiedEntry] = {}

    # ------------------------------------------------------------------
    # DBLP
    # ------------------------------------------------------------------
    def dblp_search(self, query: str, max_results: int = 3) -> List[dict]:
        url = self.config.get("citation", "dblp_endpoint", default="https://dblp.org/search/publ/api")
        params = {"q": query, "format": "json", "h": max_results}
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            hits = (
                data.get("result", {})
                .get("hits", {})
                .get("hit", [])
            )
            return [h.get("info", {}) for h in hits]
        except (requests.RequestException, json.JSONDecodeError):
            return []

    def dblp_fetch_bib(self, dblp_key: str) -> Optional[str]:
        """Fetch real BibTeX for a DBLP key like conf/nips/VaswaniSPUJGKP17."""
        url = f"{self.config.get('citation', 'dblp_bib_base', default='https://dblp.org/rec')}/{dblp_key}.bib"
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.text.strip()
        except requests.RequestException:
            return None

    # ------------------------------------------------------------------
    # CrossRef
    # ------------------------------------------------------------------
    def crossref_by_doi(self, doi: str) -> Optional[str]:
        """Fetch BibTeX from CrossRef via DOI."""
        url = f"https://doi.org/{doi}"
        try:
            r = self.session.get(
                url,
                headers={"Accept": "application/x-bibtex"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            if "bibtex" in r.headers.get("content-type", "").lower():
                return r.text.strip()
            return None
        except requests.RequestException:
            return None

    def crossref_search(self, query: str, max_results: int = 5) -> List[dict]:
        """Search CrossRef API for a title query."""
        url = "https://api.crossref.org/works"
        params = {"query.title": query, "rows": max_results, "select": "title,DOI,author,issued,container-title,volume,page"}
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", [])
            return items
        except (requests.RequestException, json.JSONDecodeError):
            return []

    def crossref_entry_to_bibtex(self, item: dict, key: str) -> Optional[str]:
        """Convert a CrossRef work item to BibTeX text."""
        try:
            title = item.get("title", [""])[0]
            authors = item.get("author", [])
            author_str = " and ".join(
                f"{a.get('family', '')}, {a.get('given', '')}" for a in authors
            )
            year = ""
            issued = item.get("issued", {}).get("date-parts", [[None]])
            if issued and issued[0] and issued[0][0]:
                year = str(issued[0][0])
            container = item.get("container-title", [""])
            container = container[0] if container else ""
            doi = item.get("DOI", "")
            volume = item.get("volume", "")
            page = item.get("page", "")
            entry = f"@article{{{key},\n  title = {{{title}}},\n  author = {{{author_str}}},\n  year = {{{year}}},"
            if container:
                entry += f"\n  journal = {{{container}}},"
            if volume:
                entry += f"\n  volume = {{{volume}}},"
            if page:
                entry += f"\n  pages = {{{page}}},"
            if doi:
                entry += f"\n  doi = {{{doi}}},"
            entry += "\n}"
            return entry
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Title matching
    # ------------------------------------------------------------------
    @staticmethod
    def _title_matches(query: str, title: str) -> bool:
        """Token-overlap heuristic: does the title plausibly match the query?

        Queries are expected to be exact-ish paper titles (possibly with a
        "(First Author et al., YEAR)" suffix, which is stripped). Returns
        True when a strong overlap exists.
        """
        # strip author-year suffix: "Attention Is All You Need (Vaswani, 2017)"
        q_clean = re.sub(r"\(.*\)\s*$", "", query).strip()
        # also strip year like ", 2017" at end
        q_clean = re.sub(r",?\s*\d{4}\s*$", "", q_clean).strip()

        def tokens(s: str):
            return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if len(w) > 2}

        q = tokens(q_clean)
        t = tokens(title)
        if not q:
            return True
        if len(q) <= 3:
            return q.issubset(t)
        # strict: candidate title must contain every significant query token
        # (token order is ignored, but all tokens must be present)
        return q.issubset(t)

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------
    def resolve_query(self, query: str) -> Optional[VerifiedEntry]:
        """Resolve a citation *topic hint* to a verified BibTeX entry.

        Strategy: try DBLP (title-similarity filtered), then CrossRef. If both
        fail, return None (the caller decides whether to leave a placeholder
        for manual fill).
        """
        if query in self._cache:
            return self._cache[query]

        # 1. DBLP search by query -- filter by title similarity
        dblp_hits = self.dblp_search(query)
        for hit in dblp_hits:
            hit_title = (hit.get("title") or "").replace("{", "").replace("}", "")
            if not self._title_matches(query, hit_title):
                continue
            dblp_key = hit.get("key", "")
            bib = self.dblp_fetch_bib(dblp_key) if dblp_key else None
            if bib:
                title = hit_title
                year = str(hit.get("year", ""))
                venue = hit.get("venue", "")
                first_author = hit.get("authors", {}).get("author", "")
                if isinstance(first_author, list):
                    first_author = first_author[0].get("text", "") if first_author else ""
                key = make_key(title, year, first_author)
                entry = VerifiedEntry(
                    key=key, bibtex=bib, title=title, year=year,
                    venue=venue, source="DBLP", verified=True,
                )
                self._cache[query] = entry
                return entry

        # 2. CrossRef search -- filter by title similarity
        cr_items = self.crossref_search(query)
        for item in cr_items:
            title = (item.get("title") or [""])[0]
            if not self._title_matches(query, title):
                continue
            authors = item.get("author", [])
            first_author = authors[0].get("family", "") if authors else ""
            year = ""
            issued = item.get("issued", {}).get("date-parts", [[None]])
            if issued and issued[0] and issued[0][0]:
                year = str(issued[0][0])
            key = make_key(title, year, first_author)
            bib = self.crossref_entry_to_bibtex(item, key)
            if bib:
                entry = VerifiedEntry(
                    key=key, bibtex=bib, title=title, year=year,
                    venue=(item.get("container-title", [""]) or [""])[0],
                    source="CrossRef", verified=True,
                )
                self._cache[query] = entry
                return entry

        # 3. Also try direct DOI hint (if query looks like a DOI)
        if re.match(r"^10\.\d{4,9}/", query):
            bib = self.crossref_by_doi(query)
            if bib:
                entry = VerifiedEntry(
                    key="doi" + query.replace("10.", "").replace("/", "").replace(".", "")[:12],
                    bibtex=bib, title=query, source="CrossRef", verified=True,
                )
                self._cache[query] = entry
                return entry

        self._cache[query] = VerifiedEntry(
            key="unresolved", bibtex="", title=query, source="UNVERIFIED", verified=False,
        )
        return self._cache[query]


def extract_cited_keys(bib_text: str) -> List[str]:
    """Return the citation keys present in a bib file."""
    keys = re.findall(r"@\w+\{([^,]+),", bib_text)
    return [k.strip() for k in keys]


def write_bibliography(entries: List[VerifiedEntry], path: Path) -> int:
    """Write a .bib file from verified entries, deduped by key. Returns entry count."""
    seen: Dict[str, str] = {}
    for e in entries:
        if not e.bibtex:
            continue
        if e.key in seen:
            continue
        seen[e.key] = e.bibtex
    lines = []
    for key in sorted(seen):
        lines.append(seen[key])
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(seen)
