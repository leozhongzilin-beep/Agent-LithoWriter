"""Identifier generators for the Literature Knowledge Base.

Frozen identity contract (see Literature_Knowledge_Base_RAG_Spec_v1.0.md):

    paper_id       — human-readable internal identity,  {DOMAIN}_{YEAR}_{NNN}
    doi            — external canonical identity (never generated here)
    source_hash    — integrity / change-detection (sha256 of source bytes)
    citation_key   — writing-agent internal reference,  FirstAuthorYearShortTitle
    bibtex_key     — lowercase BibTeX artifact key (separate from citation_key)

The five identity fields are DISTINCT and must never be merged. paper_id is
assigned by the store's dedicated per-(domain, year) counter; the generators
here only format and (for citation_key) derive from metadata.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# paper_id: {DOMAIN}_{YEAR}_{NNN}
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN = "ILT"


def format_paper_id(domain: str, year: int, seq: int) -> str:
    """Format a paper_id like ``ILT_2024_031``."""
    return f"{domain.upper()}_{int(year):04d}_{int(seq):03d}"


# ---------------------------------------------------------------------------
# Sub-object ids: {paper_id}.{kind}{NNN} — assigned in source order at import
# so re-imports are deterministic.
# ---------------------------------------------------------------------------

_KIND_TAGS = {
    "method": "md",
    "metric": "mt",
    "comparison": "cm",
    "claim": "cl",
    "evidence": "ev",
    "formula": "fm",
    "record": "cr",  # citation record (per style)
    "chunk": "ch",   # L4 paragraph chunk
}


def format_sub_id(paper_id: str, kind: str, n: int) -> str:
    """Format a sub-object id like ``ILT_2024_031.ev007``.

    kind must be one of: method, metric, comparison, claim, evidence, formula,
    record. n is a 1-based sequence in source order.
    """
    tag = _KIND_TAGS.get(kind)
    if tag is None:
        raise ValueError(f"unknown sub-id kind: {kind!r}")
    return f"{paper_id}.{tag}{int(n):03d}"


# ---------------------------------------------------------------------------
# citation_key: FirstAuthorYearShortTitle  (CamelCase, distinct from BibTeX key)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "of", "for", "on", "and", "in", "to", "with", "based",
    "is", "are", "was", "were", "at", "by", "from", "as", "that", "this",
    "using", "via",
}


def _alpha_words(text: str) -> list:
    return [w for w in re.sub(r"[^A-Za-z0-9]+", " ", text).split() if w]


def _cap(word: str) -> str:
    """Title-case a word, preserving acronyms: ``ILT`` -> ``ILT``, ``Deep`` -> ``Deep``."""
    if word.isupper():
        return word
    return word[:1].upper() + word[1:].lower()


def significant_tokens(text: str) -> list[str]:
    """Lowercased, stopword-stripped, length>1 tokens of free text."""
    words = [w.lower() for w in _alpha_words(text)]
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _last_name(first_author: str) -> str:
    a = (first_author or "").strip()
    if not a:
        return ""
    # "Vaswani, Ashish" -> "Vaswani"; "Ashish Vaswani" -> "Vaswani"
    a = a.split(",")[0].strip()
    words = _alpha_words(a)
    if not words:
        return ""
    return re.sub(r"[^A-Za-z]", "", words[-1])


def make_citation_key(title: str, year, first_author: str = "") -> str:
    """FirstAuthorYearShortTitle, e.g. ``Zhang2024TransformerILT``.

    ShortTitle = up to two significant title words (leading stopwords and the
    first word if it's a stopword are skipped), PascalCased. The BibTeX key
    (lowercase) is generated separately by write_agent.citation.make_key and
    stored in citation_cache — never conflated with this key.
    """
    author = _last_name(first_author)
    if author:
        author = author[:1].upper() + author[1:].lower()

    words = _alpha_words(title)
    significant = [w for w in words if w.lower() not in _STOPWORDS]
    if not significant:
        significant = words
    title_part = "".join(_cap(w) for w in significant[:2])
    if not title_part:
        title_part = "Paper"

    year_part = ""
    if year:
        y = re.sub(r"[^0-9]", "", str(year))[:4]
        year_part = y if y else ""

    return f"{author}{year_part}{title_part}"


def dedupe_citation_key(existing: set, base: str) -> str:
    """Append ``_a`` / ``_b`` ... until the key is unique among ``existing``."""
    if base not in existing:
        return base
    suffix = "a"
    while f"{base}_{suffix}" in existing:
        suffix = chr(ord(suffix) + 1)
    return f"{base}_{suffix}"


# ---------------------------------------------------------------------------
# source_hash
# ---------------------------------------------------------------------------

def hash_bytes(data: bytes) -> str:
    """sha256 hex digest with a ``sha256:`` prefix."""
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path) -> str | None:
    """Hash a file's bytes; returns None if the file is unreadable/missing."""
    from pathlib import Path
    try:
        return hash_bytes(Path(path).read_bytes())
    except OSError:
        return None
