"""Retrieval return contract (spec §15) + token budget enforcement.

The router NEVER returns raw text. Every searcher returns these dataclasses;
`retrieve()` assembles a ResultSet and truncates it to the caller's budget.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate: ~4 chars/token, floor of 1."""
    if not text:
        return 1
    return max(1, len(text) // 4)


def truncate_to_budget(
    items: list[Any], budget: int, *, token_of: Any = None
) -> tuple[list[Any], bool]:
    """Drop lowest-relevance items until the running token sum fits `budget`.

    Returns (kept, truncated). `token_of` estimates a single item's tokens;
    defaults to reading its `.token_cost` attribute, else its text fields.
    """
    if budget <= 0:
        return [], bool(items)
    ranked = sorted(items, key=lambda it: getattr(it, "relevance", 0.0), reverse=True)
    kept: list[Any] = []
    total = 0
    for item in ranked:
        cost = token_of(item) if token_of else _item_cost(item)
        if cost > budget:
            continue
        if total + cost > budget:
            break
        kept.append(item)
        total += cost
    return kept, len(kept) != len(items)


def _item_cost(item: Any) -> int:
    if hasattr(item, "token_cost") and item.token_cost:
        return int(item.token_cost)
    text = getattr(item, "key_fact", None) or getattr(item, "source_text", None)
    return estimate_tokens(str(text or ""))


@dataclass
class ResultItem:
    paper_id: str
    title: str
    relevance: float
    why_relevant: str = ""
    best_use: str = ""
    key_fact: str = ""
    citation_key: str = ""
    citation: str = ""
    available_levels: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    token_cost: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResultSet:
    query: str
    mode: str
    results: list[ResultItem] = field(default_factory=list)
    next_action: str = ""
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "results": [r.to_dict() for r in self.results],
            "next_action": self.next_action,
            "truncated": self.truncated,
        }


@dataclass
class EvidenceHit:
    evidence_id: str
    paper_id: str
    source_text: str
    section: str = ""
    page: str = ""
    claim: str = ""
    confidence: float | None = None
    relevance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerifyResult:
    claim: str
    verdict: str          # supported | unsupported | unverified
    strength: str | None = None  # A | B | C | D | None
    evidence: list[EvidenceHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "strength": self.strength,
            "evidence": [e.to_dict() for e in self.evidence],
            "notes": self.notes,
        }


@dataclass
class FormulaHit:
    formula_id: str
    paper_id: str
    formula_latex: str
    formula_role: str = ""
    semantic_description: str = ""
    variables: list[dict[str, str]] = field(default_factory=list)
    source_evidence_id: str = ""
    relevance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
