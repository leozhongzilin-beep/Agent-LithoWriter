"""Hybrid relevance scoring (spec §14). Pure functions, no DB access.

    score = 0.55*bm25_norm + 0.20*recency + 0.15*metadata_match + 0.10*centrality

The DB-backed searchers call these with precomputed components so ranking stays
deterministic and unit-testable.
"""

from __future__ import annotations

from typing import Any

DEFAULT_WEIGHTS = (0.55, 0.20, 0.15, 0.10)
# used when a vector term is available (bm25, vector, recency, metadata, centrality)
HYBRID_WEIGHTS = (0.45, 0.20, 0.15, 0.10, 0.10)


def normalize(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]. Single value -> 1.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def recency(year: int | None, base_year: int = 2000, span: int = 30) -> float:
    """Linear recency, clipped to [0, 1]. Unknown year -> 0."""
    if year is None:
        return 0.0
    return max(0.0, min(1.0, (year - base_year) / span))


def metadata_match(filters: dict[str, Any], paper: dict[str, Any]) -> float:
    """Fraction of supplied filters the paper satisfies. Empty filters -> 0."""
    if not filters:
        return 0.0
    checks = []
    year = paper.get("year")
    if "year_from" in filters:
        checks.append(year is not None and year >= int(filters["year_from"]))
    if "year_to" in filters:
        checks.append(year is not None and year <= int(filters["year_to"]))
    if "domain" in filters:
        checks.append(_domain_match(paper, filters["domain"]))
    if "method" in filters:
        checks.append(_tags_match(paper.get("method_tags"), filters["method"]))
    if "venue" in filters:
        checks.append(_tags_match([str(paper.get("venue") or "")], filters["venue"]))
    if not checks:
        return 0.0
    return sum(1 for c in checks if c) / len(checks)


def _domain_match(paper: dict[str, Any], domain: str) -> bool:
    domain = str(domain).upper()
    paper_id = str(paper.get("paper_id") or "").upper()
    if paper_id.startswith(domain + "_"):
        return True
    return _tags_match(paper.get("domain_tags"), domain)


def _tags_match(tags: Any, term: str) -> bool:
    term = str(term).lower()
    for t in tags or []:
        if term in str(t).lower():
            return True
    return False


def graph_centrality(degree: int, max_degree: int) -> float:
    """Normalized citation-graph in-degree (seminality). No graph -> 0."""
    if max_degree <= 0:
        return 0.0
    return degree / max_degree


def compose_score(
    bm25_norm: float,
    recency_val: float,
    metadata_val: float,
    centrality_val: float,
    *,
    vector_norm: float | None = None,
    weights: tuple[float, ...] | None = None,
) -> float:
    """Weighted hybrid score, clamped to [0, 1].

    With `vector_norm` the hybrid weights (bm25, vector, recency, metadata,
    centrality) apply; without it the classic weights stand.
    """
    if vector_norm is not None:
        wb, wv, wr, wm, wc = weights or HYBRID_WEIGHTS
        s = wb * bm25_norm + wv * vector_norm + wr * recency_val + wm * metadata_val + wc * centrality_val
    else:
        wb, wr, wm, wc = weights or DEFAULT_WEIGHTS
        s = wb * bm25_norm + wr * recency_val + wm * metadata_val + wc * centrality_val
    return max(0.0, min(1.0, s))


def concept_expansions(
    query: str, alias_map: dict[str, list[str]]
) -> list[str]:
    """Canonical terms to append when a concept alias appears in the query.

    `alias_map`: {alias: [canonical terms]} from the (optional) concepts table.
    No-op when no alias matches — the query passes through unchanged.
    """
    q = (query or "").lower()
    out: list[str] = []
    for alias, canon in alias_map.items():
        if str(alias).lower() in q:
            out.extend(canon)
    return sorted(set(out))
