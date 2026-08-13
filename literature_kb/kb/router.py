"""Retrieval Router — intent → RoutePlan (spec §13.1, §13.3).

The writing agent declares an *intent* (not a raw "search full text" command);
the router decides which layer to start at, how to escalate, what search
strategies to use, and what token budget applies. Pure table-driven logic,
no database access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

INTENTS = (
    "DISCOVERY", "CITATION", "TECHNICAL", "RESULT",
    "FORMULA", "VERIFICATION", "COMPARISON",
)


class InvalidIntent(ValueError):
    """Raised when an unknown retrieval intent is requested."""


# intent -> (start_layer, escalation layers, default budget, strategy stack)
_ROUTE_TABLE = {
    "DISCOVERY": ("L0", ["L1"], 1000,
                  ["metadata_filter", "keyword", "bm25"]),
    "CITATION": ("L0", [], 400,
                 ["citation_key_lookup", "doi_lookup", "citation_records"]),
    "TECHNICAL": ("L1", ["L2", "L3", "L4"], 1500,
                  ["keyword", "bm25"]),
    "RESULT": ("L2", ["L3", "L4"], 1500,
               ["metric_name_filter", "condition_filter"]),
    "FORMULA": ("FORMULA", [], 800,
                ["role_filter", "keyword"]),
    "VERIFICATION": ("L3", ["L4"], 1500,
                     ["evidence_bm25", "claim_match"]),
    "COMPARISON": ("L2", ["L3", "L4"], 1200,
                   ["comparisons", "evidence_cross_check"]),
}


@dataclass
class RoutePlan:
    mode: str
    start_layer: str
    escalation: list[str] = field(default_factory=list)
    budget: int = 1000
    strategy: list[str] = field(default_factory=list)


def route(intent: str, *, max_tokens: int | None = None) -> RoutePlan:
    """Translate an intent into a retrieval plan.

    `max_tokens` overrides the per-intent default budget.
    """
    mode = intent.upper()
    if mode not in _ROUTE_TABLE:
        raise InvalidIntent(f"unknown retrieval intent: {intent!r}")
    start, escalation, default_budget, strategy = _ROUTE_TABLE[mode]
    return RoutePlan(
        mode=mode,
        start_layer=start,
        escalation=list(escalation),
        budget=default_budget if max_tokens is None else int(max_tokens),
        strategy=list(strategy),
    )
