"""L0/L1/L2 searchers (spec §13). Metadata filter + FTS5 BM25 + hybrid relevance.

Every searcher returns the contract's `ResultItem`s (never raw text), ranked by
`relevance.compose_score`, with citation_key, a rendered short citation, and
evidence ids so each result stays traceable.
"""

from __future__ import annotations

from typing import Any

from . import fts, relevance, vectors
from .citation import short_citation
from .contract import ResultItem, estimate_tokens
from .store import KBStore, _jload

# ---------------------------------------------------------------------------
# candidate selection + ranking
# ---------------------------------------------------------------------------

def _expand_match(query: str, aliases: dict[str, list[str]] | None) -> str:
    """FTS expression: literal AND-joined query, OR-ed with expansion phrases.

    Concept expansion is recall-side: ``AI-ILT`` matches the literal AND query
    *or* any expanded phrase (e.g. ``"Neural mask synthesis"``) — never
    AND-constrained by the expansion terms.
    """
    base = fts.make_match(query)
    if not aliases:
        return base
    extra = relevance.concept_expansions(query, aliases)
    if not extra:
        return base
    exp = " OR ".join(f'"{term}"' for term in extra)
    return f"({base}) OR ({exp})" if base else exp


def _normalize_bm25(cand: dict[str, float]) -> dict[str, float]:
    """Min-max normalize FTS scores to [0,1]; no-FTS candidates get 0.

    (FTS scores are positive here — higher = better; 0.0 means 'no FTS match'
    and must not be inflated by min-max.)
    """
    vals = [v for v in cand.values() if v > 0.0]
    if not vals:
        return {pid: 0.0 for pid in cand}
    lo, hi = min(vals), max(vals)
    out: dict[str, float] = {}
    for pid, s in cand.items():
        if s <= 0.0:
            out[pid] = 0.0
        elif lo == hi:
            out[pid] = 1.0
        else:
            out[pid] = (s - lo) / (hi - lo)
    return out


def _candidates(
    store: KBStore,
    query: str,
    filters: dict[str, Any] | None,
    aliases: dict[str, list[str]] | None,
    limit: int = 100,
) -> list[tuple[str, float]]:
    """Candidate (paper_id, bm25_norm) pairs: FTS ∩ metadata, with LIKE fallback."""
    cand: dict[str, float] = {}
    if query.strip():
        for h in fts.query(store.conn, "fts_papers", query, limit=limit,
                           match_expr=_expand_match(query, aliases)):
            cand[h["paper_id"]] = h["score"]

    if filters:
        allowed = set(store.filter_papers(filters, limit=limit))
        if cand:
            cand = {pid: s for pid, s in cand.items() if pid in allowed}
        else:
            cand = {pid: 0.0 for pid in allowed}
    elif not query.strip() and not cand:
        cand = {pid: 0.0 for pid in store.all_paper_ids(limit=limit)}
    elif not cand:
        for pid in store.like_papers(query, limit=limit):
            cand[pid] = 0.0

    norms = _normalize_bm25(cand)
    return [(pid, norms[pid]) for pid in cand]


def _rank(
    store: KBStore,
    candidates: list[tuple[str, float]],
    filters: dict[str, Any] | None,
    limit: int,
    vec_norms: dict[str, float] | None = None,
) -> list[tuple[float, str, dict[str, Any]]]:
    degrees = store.citation_in_degrees()
    max_deg = max(degrees.values()) if degrees else 0
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for pid, bm25_norm in candidates:
        paper = store.get_paper(pid)
        if paper is None:
            continue
        vec = (vec_norms or {}).get(pid)
        s = relevance.compose_score(
            bm25_norm,
            relevance.recency(paper.get("year")),
            relevance.metadata_match(filters or {}, paper),
            relevance.graph_centrality(degrees.get(pid, 0), max_deg),
            vector_norm=vec,
        )
        scored.append((s, pid, paper))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# result assembly
# ---------------------------------------------------------------------------

def _resultitem(
    store: KBStore, pid: str, paper: dict[str, Any], score: float,
    *, key_fact: str | None = None, best_use: str = "",
) -> ResultItem:
    kf = key_fact if key_fact is not None else (paper.get("one_line_description") or "")
    return ResultItem(
        paper_id=pid,
        title=paper.get("title") or "",
        relevance=score,
        best_use=best_use,
        key_fact=kf,
        citation_key=paper.get("citation_key") or "",
        citation=short_citation(paper),
        available_levels=store.available_levels(pid),
        evidence_ids=store.evidence_ids_for(pid, limit=3),
        token_cost=estimate_tokens(kf) + 20,
    )


# ---------------------------------------------------------------------------
# L0 / L1 / L2
# ---------------------------------------------------------------------------

def search_l0(
    store: KBStore,
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 10,
    aliases: dict[str, list[str]] | None = None,
    embedder=None,
) -> list[ResultItem]:
    """Discovery: L0 index cards, ranked. Metadata filters honored.

    With an `embedder`, vector search unions semantically-close papers that the
    lexical pass missed, and the vector term is fused into the hybrid score.
    """
    candidates = _candidates(store, query, filters, aliases)
    vec_norms = _vector_union(store, query, candidates, embedder, limit)
    return [
        _resultitem(store, pid, paper, score)
        for score, pid, paper in _rank(store, candidates, filters, limit, vec_norms)
    ]


def _vector_union(
    store: KBStore,
    query: str,
    candidates: list[tuple[str, float]],
    embedder,
    limit: int,
) -> dict[str, float]:
    """Union vector candidates into `candidates`; return normalized vector terms.

    Papers embedded close to the query but absent lexically are rescued here;
    papers without a stored embedding get no vector term (score 0).
    """
    if embedder is None or not query.strip():
        return {}
    hits = vectors.search_vectors(store, embedder, query, "paper", limit=limit)
    if not hits:
        return {}
    present = {pid for pid, _ in candidates}
    for h in hits:
        if h["score"] > 0 and h["paper_id"] not in present:
            candidates.append((h["paper_id"], 0.0))  # vector-only addition
    vmax = max(h["score"] for h in hits)
    return {
        h["paper_id"]: (h["score"] / vmax if vmax > 0 else 0.0)
        for h in hits if h["score"] > 0
    }


def search_l1(
    store: KBStore,
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 5,
    aliases: dict[str, list[str]] | None = None,
) -> list[ResultItem]:
    """TECHNICAL: papers whose L1 card summarizes the match."""
    candidates = _candidates(store, query, filters, aliases)
    out: list[ResultItem] = []
    for score, pid, paper in _rank(store, candidates, filters, limit):
        summary = _card_summary(store, pid)
        out.append(_resultitem(store, pid, paper, score, key_fact=summary))
    return out


def _card_summary(store: KBStore, pid: str) -> str:
    row = store.conn.execute(
        "SELECT method_summary, main_idea, abstract FROM paper_cards WHERE paper_id = ?",
        (pid,),
    ).fetchone()
    if row is None:
        return ""
    return row["method_summary"] or row["main_idea"] or row["abstract"] or ""


def search_l2(
    store: KBStore,
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 10,
) -> list[ResultItem]:
    """RESULT: papers with a metric whose name/condition matches the query."""
    by_paper: dict[str, dict[str, Any]] = {}
    for m in store.metrics_matching(query, limit=100):
        by_paper.setdefault(m["paper_id"], m)
    if filters:
        allowed = set(store.filter_papers(filters, limit=200))
        by_paper = {pid: m for pid, m in by_paper.items() if pid in allowed}
    candidates = [(pid, 0.0) for pid in by_paper]
    out: list[ResultItem] = []
    for score, pid, paper in _rank(store, candidates, filters, limit):
        m = by_paper[pid]
        out.append(_resultitem(
            store, pid, paper, score,
            key_fact=_format_metric(m), best_use="structured results",
        ))
    return out


def _format_metric(m: dict[str, Any]) -> str:
    name = m.get("name") or ""
    val = m.get("value_text")
    if val is None and m.get("value") is not None:
        val = str(m["value"])
    unit = f" {m['unit']}" if m.get("unit") else ""
    cond = _jload(m.get("condition")) or {}
    cond_str = ", ".join(f"{k}={v}" for k, v in cond.items())
    tail = f" ({cond_str})" if cond_str else ""
    return f"{name}={val or '?'}{unit}{tail}"


def search_l4(
    store: KBStore,
    query: str,
    *,
    paper_ids: list[str] | None = None,
    limit: int = 10,
) -> list[ResultItem]:
    """L4: full-text paragraph chunks matching the query, best-first.

    Chunks carry their section and chunk_id so the writing agent can quote the
    full-text context the L3 evidence pointed at.
    """
    hits = fts.query(store.conn, "fts_chunks", query, limit=limit)
    if paper_ids:
        allowed = set(paper_ids)
        hits = [h for h in hits if h["paper_id"] in allowed]
    if not hits:
        return []
    scores = [h["score"] for h in hits]
    norm = relevance.normalize(scores)
    out: list[ResultItem] = []
    for h, rel in zip(hits, norm, strict=True):
        paper = store.get_paper(h["paper_id"]) or {}
        section = h.get("section") or ""
        out.append(ResultItem(
            paper_id=h["paper_id"],
            title=paper.get("title") or "",
            relevance=rel,
            best_use=f"L4 chunk · {section}".rstrip(" ·"),
            key_fact=h.get("text") or "",
            citation_key=paper.get("citation_key") or "",
            available_levels=["L4"],
            evidence_ids=[h["chunk_id"]] if h.get("chunk_id") else [],
            token_cost=estimate_tokens(h.get("text")),
        ))
    return out[:limit]


# ---------------------------------------------------------------------------
# L1 / L2 full reads
# ---------------------------------------------------------------------------

def get_paper_card(store: KBStore, paper_id: str) -> dict[str, Any] | None:
    """Full L1 paper card."""
    row = store.conn.execute(
        "SELECT * FROM paper_cards WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    for col in ("main_contributions", "limitations"):
        d[col] = _jload(d.get(col))
    d["recommended_use"] = _jload(d.get("recommended_use"))
    paper = store.get_paper(paper_id)
    d["title"] = paper["title"] if paper else ""
    return d


def get_structured_results(
    store: KBStore, paper_id: str, metrics: list[str] | None = None
) -> dict[str, Any]:
    """L2 metric cards (with conditions + evidence) and comparisons.

    When the metrics ontology is seeded, each metric card also carries its
    `comparability_rules` / `common_pitfalls` so the writing agent knows when
    two numbers must NOT be ranked together.
    """
    metric_rows = store.metric_rows_for(paper_id, metrics)
    _attach_ontology(store, metric_rows)
    return {
        "paper_id": paper_id,
        "metrics": metric_rows,
        "comparisons": store.comparison_rows_for(paper_id),
    }


def _attach_ontology(store: KBStore, metrics: list[dict[str, Any]]) -> None:
    if not metrics:
        return
    names = [m["name"] for m in metrics]
    placeholders = ", ".join("?" * len(names))
    rows = store.conn.execute(
        f"SELECT metric_name, comparability_rules, common_pitfalls "
        f"FROM metrics_ontology WHERE metric_name IN ({placeholders})",
        names,
    ).fetchall()
    for m in metrics:
        for r in rows:
            if r["metric_name"] == m["name"]:
                if r["comparability_rules"]:
                    m["comparability_rules"] = r["comparability_rules"]
                if r["common_pitfalls"]:
                    m["common_pitfalls"] = r["common_pitfalls"]
                break
