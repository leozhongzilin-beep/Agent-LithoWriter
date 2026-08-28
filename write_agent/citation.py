"""Citation verification for the writing agent.

Prevents hallucinated BibTeX. Citation verification strategy:
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
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config
from .kb_bridge import KbProvider, build_kb_provider


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

    def __init__(self, config: Config, timeout: int = 20,
                 kb: KbProvider | None = None):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "writing-agent/0.1 (academic citation verifier)"})
        self._cache: dict[str, VerifiedEntry] = {}
        self.kb = kb if kb is not None else build_kb_provider(config)

    # ------------------------------------------------------------------
    # DBLP
    # ------------------------------------------------------------------
    def dblp_search(self, query: str, max_results: int = 3) -> list[dict]:
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

    def dblp_fetch_bib(self, dblp_key: str) -> str | None:
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
    def crossref_by_doi(self, doi: str) -> str | None:
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

    def crossref_search(self, query: str, max_results: int = 5) -> list[dict]:
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

    def crossref_entry_to_bibtex(self, item: dict, key: str) -> str | None:
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
    _STOPWORDS = {
        "for", "and", "the", "with", "via", "from", "using", "into",
        "toward", "towards", "based",
    }

    @staticmethod
    def _strip_citation_suffix(query: str) -> str:
        """Remove an author-year suffix so the bare title can be searched.

        Handles "Title (First Author et al., YEAR)" and trailing ", YEAR".
        """
        q_clean = re.sub(r"\(.*\)\s*$", "", query).strip()  # "(Vaswani, 2017)"
        return re.sub(r",?\s*\d{4}\s*$", "", q_clean).strip()  # ", 2017"

    @staticmethod
    def _sig_tokens(s: str) -> set:
        """Normalize to significant word tokens.

        Hyphenated compounds ("GAN-OPC") stay atomic AND are also split into
        their parts ("gan", "opc") so matching is robust to whether a source
        writes the acronym as one token or as separate words.
        """
        s = re.sub(r"[^a-z0-9\- ]", " ", s.lower())
        out = set()
        for w in s.split():
            if len(w) <= 2 or w in CitationResolver._STOPWORDS:
                continue
            out.add(w)
            out.update(p for p in w.split("-") if len(p) > 2)
        return out

    @staticmethod
    def _head(s: str) -> str:
        """Leading colon-separated segment, normalized ('' when no ':').

        "GAN-OPC: Mask Optimization ..." -> "ganopc"; a colon-less string
        yields its fully-normalized self, which rarely equals another title.
        """
        seg = s.split(":", 1)[0].strip()
        return re.sub(r"[^a-z0-9]", "", seg.lower())

    @staticmethod
    def _distinctive(tok: str) -> bool:
        """True for high-identity tokens: acronyms, years, hyphenated names.

        A match built only from generic words ("inverse lithography
        technology") must not be enough to attach a specific paper.
        """
        return "-" in tok or tok.isdigit() or 2 <= len(tok) <= 3

    @classmethod
    def _title_matches(cls, query: str, title: str) -> bool:
        """Fuzzy title match, tolerant of LLM-rewritten subtitles.

        The planning phase often rewrites a paper's subtitle (e.g. expands an
        acronym) or rephrases it, so a strict token-subset gate rejects titles
        that are clearly the same paper. Four progressively weaker signals are
        checked; partial-subset and coverage matches require at least one
        *distinctive* shared token so generic phrases cannot cross-match
        unrelated papers:

        1. Distinctive leading name ("GAN-OPC:", "Neural-ILT:") is decisive.
        2. Exact token-set equality.
        3. Containment, gated on a distinctive shared token.
        4. Strong query coverage (>= 60%), gated on a distinctive shared token.
        5. Strong symmetric overlap (Jaccard >= 0.5).
        """
        q_clean = cls._strip_citation_suffix(query)
        q = cls._sig_tokens(q_clean)
        t = cls._sig_tokens(title)
        if not q or not t:
            return False

        q_head, t_head = cls._head(q_clean), cls._head(title)
        if q_head and t_head and len(q_head) >= 3 and q_head == t_head:
            return True

        ov = q & t
        if not ov:
            return False
        distinctive = any(cls._distinctive(x) for x in ov)
        if q == t:
            return True
        if q < t or t < q:
            return distinctive
        if len(ov) >= 2 and len(ov) / len(q) >= 0.6:
            return distinctive
        return len(ov) / len(q | t) >= 0.5

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------
    def resolve_query(self, query: str) -> VerifiedEntry | None:
        """Resolve a citation *topic hint* to a verified BibTeX entry.

        Strategy: try DBLP (title-similarity filtered), then CrossRef. If both
        fail, return None (the caller decides whether to leave a placeholder
        for manual fill).
        """
        if query in self._cache:
            return self._cache[query]

        if self.kb is not None:
            entry = self._resolve_from_kb(query)
            if entry is not None:
                self._cache[query] = entry
                return entry

        if not self.config.dblp_verify:
            self._cache[query] = VerifiedEntry(
                key="unresolved", bibtex="", title=query, source="UNVERIFIED", verified=False,
            )
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

    def _resolve_from_kb(self, query: str) -> VerifiedEntry | None:
        """Resolve a hint against the Literature KB, or None to fall through.

        Acceptance: non-empty BibTeX AND (the hint IS the KB citation_key OR
        the stored title passes the strict title-match gate). The draft cite
        key is the stored BibTeX's own internal key — never rewritten.
        """
        if self.kb is None:
            return None
        clean = self._strip_citation_suffix(query)  # KB title search needs the bare title
        for cand in self.kb.resolve_hint(clean):
            if not cand.bibtex:
                continue
            if cand.citation_key != query and not self._title_matches(query, cand.title):
                continue
            keys = extract_cited_keys(cand.bibtex)
            if not keys:
                continue
            return VerifiedEntry(
                key=keys[0],
                bibtex=cand.bibtex,
                title=cand.title,
                year=cand.year,
                venue=cand.venue,
                source="KB",
                verified=True,
            )
        return None


def extract_cited_keys(bib_text: str) -> list[str]:
    """Return the citation keys present in a bib file."""
    keys = re.findall(r"@\w+\{([^,]+),", bib_text)
    return [k.strip() for k in keys]


def write_bibliography(entries: list[VerifiedEntry], path: Path) -> int:
    """Write a .bib file from verified entries, deduped by key. Returns entry count."""
    seen: dict[str, str] = {}
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
