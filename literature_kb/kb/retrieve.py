"""RetrievalService — the single facade the writing agent calls.

    retrieve(query, intent, filters?, max_tokens?) -> ResultSet

Runs the router's plan (spec §13): intent -> start layer -> per-layer
searcher -> relevance ranking -> budget truncation -> next_action hint.
VERIFICATION and FORMULA return contract-shaped ResultItems too; the
structured methods (verify / formulas / cite / card / structured) expose the
full typed objects for direct use.
"""

from __future__ import annotations

from typing import Any

from . import contract, evidence, formula, ontology, router, search
from .citation import resolve_citation
from .contract import ResultItem, ResultSet
from .store import KBStore

_LAYER_NEXT = {
    "L0": ("L1", "escalate to L1 for method details"),
    "L1": ("L2", "escalate to L2 for structured results"),
    "L2": ("L3", "escalate to L3 for evidence trace"),
    "L3": ("L4", "escalate to L4 for full-text context"),
}


class RetrievalService:
    def __init__(self, store: KBStore):
        self.store = store

    # ------------------------------------------------------------------
    # main facade
    # ------------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        intent: str,
        *,
        filters: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        aliases: dict[str, list[str]] | None = None,
    ) -> ResultSet:
        plan = router.route(intent, max_tokens=max_tokens)
        # activate concept expansion when the ontology is seeded
        if aliases is None and ontology.has_concepts(self.store):
            aliases = ontology.alias_map(self.store)
        if plan.mode == "CITATION":
            return self._retrieve_citation(query, plan)
        if plan.mode == "FORMULA":
            return self._retrieve_formulas(query, plan)
        if plan.mode == "VERIFICATION":
            return self._retrieve_verification(query, plan)

        if plan.mode == "DISCOVERY":
            items = search.search_l0(
                self.store, query, filters=filters, aliases=aliases, limit=10)
        elif plan.mode == "TECHNICAL":
            items = search.search_l1(
                self.store, query, filters=filters, aliases=aliases, limit=5)
        else:  # RESULT / COMPARISON
            items = search.search_l2(
                self.store, query, filters=filters, limit=10)

        next_layer, hint = _LAYER_NEXT.get(plan.start_layer, ("", ""))
        next_action = hint if items and next_layer in plan.escalation else ""
        kept, truncated = contract.truncate_to_budget(items, plan.budget)
        return ResultSet(query=query, mode=plan.mode, results=kept,
                         next_action=next_action, truncated=truncated)

    # ------------------------------------------------------------------
    # intent-specific retrieval
    # ------------------------------------------------------------------
    def _retrieve_citation(self, query: str, plan: router.RoutePlan) -> ResultSet:
        pid = (
            self.store.find_by_citation_key(query)
            or self.store.find_by_doi(query)
            or (query if self.store.paper_exists(query) else None)
        )
        if pid is None:
            items = search.search_l0(self.store, query, limit=1)
            pid = items[0].paper_id if items else None
        if pid is None:
            return ResultSet(query=query, mode="CITATION", next_action="")
        paper = self.store.get_paper(pid) or {}
        item = ResultItem(
            paper_id=pid,
            title=paper.get("title") or "",
            relevance=1.0,
            key_fact=paper.get("one_line_description") or "",
            citation_key=paper.get("citation_key") or "",
            citation=resolve_citation(self.store, pid)["in_text_citation"],
            available_levels=["L0"],
            evidence_ids=self.store.evidence_ids_for(pid, limit=3),
        )
        return ResultSet(query=query, mode="CITATION", results=[item])

    def _retrieve_formulas(self, query: str, plan: router.RoutePlan) -> ResultSet:
        hits = formula.search_formulas(self.store, query, limit=10)
        items = [self._formula_item(f) for f in hits]
        kept, truncated = contract.truncate_to_budget(items, plan.budget)
        return ResultSet(query=query, mode="FORMULA", results=kept,
                         truncated=truncated)

    def _retrieve_verification(self, query: str, plan: router.RoutePlan) -> ResultSet:
        v = evidence.verify_claim(self.store, query)
        items = [self._evidence_item(e) for e in v.evidence]
        kept, truncated = contract.truncate_to_budget(items, plan.budget)
        next_action = "escalate to L4 for full-text context" if (
            kept and "L4" in plan.escalation
        ) else ""
        return ResultSet(query=query, mode="VERIFICATION", results=kept,
                         next_action=next_action, truncated=truncated)

    # ------------------------------------------------------------------
    # structured methods (full typed objects)
    # ------------------------------------------------------------------
    def get_card(self, paper_id: str) -> dict[str, Any] | None:
        return search.get_paper_card(self.store, paper_id)

    def structured(self, paper_id: str, metrics: list[str] | None = None) -> dict[str, Any]:
        return search.get_structured_results(self.store, paper_id, metrics)

    def verify(self, claim: str, candidate_papers: list[str] | None = None):
        return evidence.verify_claim(self.store, claim,
                                     candidate_papers=candidate_papers)

    def formulas(self, query: str, role: str | None = None):
        return formula.search_formulas(self.store, query, role=role)

    def l4(self, query: str, paper_ids: list[str] | None = None):
        """L4 full-text chunk search — the terminal escalation step."""
        return search.search_l4(self.store, query, paper_ids=paper_ids)

    def cite(self, paper_id: str, style_id: str | None = None):
        return resolve_citation(self.store, paper_id, style_id)

    # ------------------------------------------------------------------
    # mapping to ResultItem
    # ------------------------------------------------------------------
    def _formula_item(self, f: contract.FormulaHit) -> ResultItem:
        paper = self.store.get_paper(f.paper_id) or {}
        kf = f"[{f.formula_role}] {f.formula_latex} — {f.semantic_description}"
        return ResultItem(
            paper_id=f.paper_id,
            title=paper.get("title") or "",
            relevance=f.relevance,
            key_fact=kf.strip(" —"),
            citation_key=paper.get("citation_key") or "",
            available_levels=["FORMULA"],
            evidence_ids=[f.source_evidence_id] if f.source_evidence_id else [],
            token_cost=contract.estimate_tokens(f.formula_latex)
            + contract.estimate_tokens(f.semantic_description),
        )

    def _evidence_item(self, e: contract.EvidenceHit) -> ResultItem:
        paper = self.store.get_paper(e.paper_id) or {}
        return ResultItem(
            paper_id=e.paper_id,
            title=paper.get("title") or "",
            relevance=e.relevance,
            key_fact=e.source_text,
            citation_key=paper.get("citation_key") or "",
            available_levels=["L3"],
            evidence_ids=[e.evidence_id],
            token_cost=contract.estimate_tokens(e.source_text),
        )
