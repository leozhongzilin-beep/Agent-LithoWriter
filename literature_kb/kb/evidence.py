"""L3 evidence search + claim verification (spec §16).

`search_evidence` returns verbatim evidence hits with paper/section/page.
`verify_claim` never upgrades evidence: no evidence -> unverified; a stored
claim's strength (A/B/C/D) is surfaced only when its text actually shares
tokens with the query — never invented.
"""

from __future__ import annotations

from typing import Any

from . import fts, relevance
from .contract import EvidenceHit, VerifyResult
from .ids import significant_tokens
from .store import KBStore

_STRENGTH_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


def search_evidence(
    store: KBStore,
    query: str,
    *,
    paper_ids: list[str] | None = None,
    limit: int = 10,
    match_expr: str | None = None,
) -> list[EvidenceHit]:
    """Evidence blocks matching the query, best-first, verbatim text preserved.

    `match_expr` bypasses tokenization for callers with a pre-built FTS
    expression (e.g. OR-joined verification queries).
    """
    allowed = set(paper_ids) if paper_ids else None
    scores: dict[str, float] = {}
    cand_ids: list[str] = []

    hits = fts.query(store.conn, "fts_evidence", query, limit=limit,
                     match_expr=match_expr)
    for h in hits:
        if allowed is None or h["paper_id"] in allowed:
            cand_ids.append(h["evidence_id"])
            scores[h["evidence_id"]] = h["score"]

    if not cand_ids and query.strip():
        q = f"%{query}%"
        rows = store.conn.execute(
            "SELECT evidence_id, paper_id FROM paper_evidence "
            "WHERE source_text LIKE ? OR claim LIKE ? LIMIT ?",
            (q, q, limit),
        ).fetchall()
        for r in rows:
            if allowed is None or r["paper_id"] in allowed:
                cand_ids.append(r["evidence_id"])

    if not cand_ids:
        return []

    placeholders = ", ".join("?" * len(cand_ids))
    rows_by_id: dict[str, Any] = {}
    for row in store.conn.execute(
        f"SELECT * FROM paper_evidence WHERE evidence_id IN ({placeholders})",
        cand_ids,
    ).fetchall():
        rows_by_id[row["evidence_id"]] = dict(row)

    norm = relevance.normalize([scores.get(eid, 0.0) for eid in cand_ids])
    out: list[EvidenceHit] = []
    for eid, rel in zip(cand_ids, norm, strict=True):
        row = rows_by_id[eid]
        out.append(EvidenceHit(
            evidence_id=eid,
            paper_id=row["paper_id"],
            source_text=row.get("source_text") or "",
            section=row.get("section") or "",
            page=row.get("page") or "",
            claim=row.get("claim") or "",
            confidence=row.get("confidence"),
            relevance=rel,
        ))
    out.sort(key=lambda e: e.relevance, reverse=True)
    return out[:limit]


def verify_claim(
    store: KBStore,
    claim: str,
    *,
    candidate_papers: list[str] | None = None,
    limit: int = 10,
) -> VerifyResult:
    """Check whether `claim` is supported by stored evidence.

    Verdicts: supported (evidence found) | unverified (no evidence).
    strength: the stored claim strength (A/B/C/D) only when a stored claim
    shares tokens with the query; None otherwise.
    """
    tokens = significant_tokens(claim)
    if not tokens:
        return VerifyResult(claim=claim, verdict="unverified",
                            notes=["no significant tokens"])

    or_match = " OR ".join(f'"{t}"' for t in tokens)
    hits = search_evidence(
        store, claim, paper_ids=candidate_papers, limit=limit,
        match_expr=or_match,
    )
    # precision gate: a hit must cover >= half of the claim's significant
    # tokens, else a single shared word would "verify" an unrelated claim.
    qualified = [h for h in hits if _coverage(h, tokens) >= 0.5]
    if not qualified:
        return VerifyResult(claim=claim, verdict="unverified",
                            notes=["no evidence covering the claim's content"])

    strength = _best_claim_strength(
        store, tokens, {h.paper_id for h in qualified}
    )
    return VerifyResult(
        claim=claim,
        verdict="supported",
        strength=strength,
        evidence=qualified,
    )


def _coverage(hit: EvidenceHit, tokens: list[str]) -> float:
    """Fraction of significant tokens present in an evidence hit's text."""
    text = f"{hit.source_text} {hit.claim}".lower()
    covered = sum(1 for t in tokens if t in text)
    return covered / len(tokens) if tokens else 0.0


def _best_claim_strength(
    store: KBStore, tokens: list[str], paper_ids: set[str]
) -> str | None:
    if not paper_ids:
        return None
    placeholders = ", ".join("?" * len(paper_ids))
    rows = store.conn.execute(
        f"SELECT claim, strength FROM paper_claims WHERE paper_id IN ({placeholders})",
        list(paper_ids),
    ).fetchall()
    best: str | None = None
    best_rank = 0
    for r in rows:
        text = str(r["claim"] or "").lower()
        if not any(t in text for t in tokens):
            continue
        rank = _STRENGTH_ORDER.get(r["strength"], 0)
        if rank > best_rank:
            best, best_rank = r["strength"], rank
    return best
