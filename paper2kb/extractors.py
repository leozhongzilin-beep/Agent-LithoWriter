"""Per-layer LLM extraction + normalization (Skill steps 2-11).

Each layer is one focused LLM call (spec: structured over raw, evidence first,
no fabrication). The system prompt encodes the skill's behavior constraints;
the per-layer user prompts are marked ``LAYER: <name>`` so tests can dispatch a
mock. Normalizers coerce every layer to the KB's package shape and enforce the
enum sets — an invalid status becomes ``unclear``, an unsubstantiated
``improves`` becomes ``cites``, an unknown variable meaning becomes ``unclear``.
"""

from __future__ import annotations

import json
from typing import Any

MAX_LAYER_CHARS = 28000

_METRIC_STATUSES = {"reported", "not_reported", "not_applicable", "unclear"}
_CLAIM_TYPES = {"definition", "methodological", "quantitative", "comparative",
                "causal", "limitation", "conclusion"}
_EVIDENCE_TYPES = {"definition", "methodological_statement", "observation",
                   "experimental_result", "comparison", "limitation",
                   "causal_claim", "quantitative_result"}
_FORMULA_ROLES = {"objective", "loss", "forward_model", "constraint",
                  "regularization", "metric", "physical_model", "evaluation",
                  "network", "update_rule"}
_GRAPH_RELATIONS = {"cites", "extends", "improves", "compares_with", "uses",
                    "criticizes", "builds_on", "same_method_family"}
_USE_TAGS = {"none", "weak", "moderate", "strong"}
_STRENGTHS = {"A", "B", "C", "D"}

_SYSTEM_PROMPT = """You are the knowledge-base construction agent for the \
paper_to_literature_kb skill. You build structured, queryable, traceable \
knowledge — you are NOT a summarizer.

Hard rules:
- NEVER fabricate. If the paper does not report a number, set status to \
"not_reported" or "unclear"; never read numbers off figures, never infer \
runtimes from architecture, never turn "significant improvement" into a \
percentage.
- Preserve experimental conditions exactly as the paper states them.
- source_text (evidence) must be a VERBATIM quote from the paper.
- A variable whose meaning is unknown is marked "unclear", never guessed.
- Do not promote a bibliography reference into an "improves"/"extends" \
relation unless the text explicitly says so; default is "cites".
- Do not merge distinct metrics or relations under one term.
"""


# ---------------------------------------------------------------------------
# prompt plumbing
# ---------------------------------------------------------------------------

def _budget(text: str, max_chars: int = MAX_LAYER_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _with_text(user: str, doc) -> str:
    return user + "\n\nPAPER TEXT (truncated):\n" + _budget(doc.full_text)


def _layer(layer: str, body: str, doc, extra: str = "") -> str:
    return _with_text(f"LAYER: {layer}\n\n{body}\n\n{extra}".rstrip(), doc)


def _chat(llm, layer: str, body: str, doc, extra: str = "") -> dict[str, Any]:
    return llm.chat_json(_SYSTEM_PROMPT, _layer(layer, body, doc, extra))


# ---------------------------------------------------------------------------
# L0
# ---------------------------------------------------------------------------

def extract_l0(llm, doc, meta: dict[str, Any]) -> dict[str, Any]:
    body = """Extract the L0 paper index. Use ONLY the CONFIRMED METADATA for \
title/authors/year/venue/doi — do not alter it. Return JSON:
{"one_line_description": str, "keywords": [str], "domain_tags": [str], \
"method_tags": [str]}
- one_line_description: 1-2 sentences describing ONLY what the paper does; \
no praise, no unverifiable superlatives.
- domain_tags: research domain, e.g. ["ILT"], ["OPC"], ["SMO"].
- method_tags: the method family, e.g. ["KAN"], ["CNN"], ["Transformer"], \
["level-set"]."""
    extra = "CONFIRMED METADATA:\n" + json.dumps(meta, ensure_ascii=False)
    return _normalize_l0(_chat(llm, "l0", body, doc, extra), meta)


def _normalize_l0(raw: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out = {k: meta[k] for k in (
        "title", "authors_summary", "year", "venue", "article_type", "doi",
        "url", "volume", "issue", "pages", "publisher") if meta.get(k)}
    out["one_line_description"] = raw.get("one_line_description")
    out["keywords"] = [str(k) for k in (raw.get("keywords") or []) if k]
    out["domain_tags"] = [str(k) for k in (raw.get("domain_tags") or []) if k]
    out["method_tags"] = [str(k) for k in (raw.get("method_tags") or []) if k]
    return out


# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------

def extract_l1(llm, doc) -> dict[str, Any]:
    body = """Extract the L1 paper understanding from the abstract, \
introduction, method overview and conclusion. Return JSON:
{"abstract": str, "research_problem": str, "research_gap": str, \
"main_idea": str, "method_summary": str, "main_contributions": [str], \
"innovation": str, "key_findings_summary": str, "limitations": [str], \
"datasets_summary": str, "methods_summary": str, \
"recommended_use": {"background"/"motivation"/"related_work"/"method"/\
"discussion"/"citation_evidence": "none"|"weak"|"moderate"|"strong"}}
Do not mark a paper strong for a use just because it is in-domain."""
    return _normalize_l1(_chat(llm, "l1", body, doc))


def _normalize_l1(raw: dict[str, Any]) -> dict[str, Any]:
    ru = raw.get("recommended_use") or {}
    return {
        "abstract": raw.get("abstract"),
        "research_problem": raw.get("research_problem"),
        "research_gap": raw.get("research_gap"),
        "main_idea": raw.get("main_idea"),
        "method_summary": raw.get("method_summary"),
        "main_contributions": [str(x) for x in (raw.get("main_contributions") or [])],
        "innovation": raw.get("innovation"),
        "key_findings_summary": raw.get("key_findings_summary"),
        "limitations": [str(x) for x in (raw.get("limitations") or [])],
        "datasets_summary": raw.get("datasets_summary"),
        "methods_summary": raw.get("methods_summary"),
        "recommended_use": {
            k: v for k, v in ru.items()
            if k in ("background", "motivation", "related_work", "method",
                     "discussion", "citation_evidence") and v in _USE_TAGS
        },
    }


# ---------------------------------------------------------------------------
# L2 method + results
# ---------------------------------------------------------------------------

def extract_l2_method(llm, doc) -> dict[str, Any]:
    body = """Extract the L2 method card from the method section. Return JSON:
{"method_card": {"method_name": str, "method_family": str, "task": str, \
"input": str, "output": str, "architecture": str, "algorithm": str, \
"optimization": str, "loss_function": str, "training_strategy": str, \
"inference_strategy": str, "iterative_or_direct": "iterative"|"direct"}, \
"system_context": {"technology_node": str, "wavelength": str, \
"numerical_aperture": str, "resist_model": str, "lithography_condition": str, \
"pattern_type": str, "resolution": str}}
Use null/omit for anything the paper does not state — never infer."""
    return _normalize_l2_method(_chat(llm, "l2m", body, doc))


def _normalize_l2_method(raw: dict[str, Any]) -> dict[str, Any]:
    card = raw.get("method_card") or {}
    sysctx = raw.get("system_context") or {}
    return {
        "method_card": {
            "method_name": card.get("method_name"),
            "method_family": card.get("method_family"),
            "task": card.get("task"),
            "input": card.get("input"),
            "output": card.get("output"),
            "architecture": card.get("architecture"),
            "algorithm": card.get("algorithm"),
            "optimization": card.get("optimization"),
            "loss_function": card.get("loss_function"),
            "training_strategy": card.get("training_strategy"),
            "inference_strategy": card.get("inference_strategy"),
            "iterative_or_direct": card.get("iterative_or_direct"),
            "system_context": {
                k: sysctx.get(k) for k in (
                    "technology_node", "wavelength", "numerical_aperture",
                    "NA", "resist_model", "lithography_condition",
                    "pattern_type", "resolution")
            },
        }
    }


def extract_l2_results(llm, doc) -> dict[str, Any]:
    body = """Extract the L2 result card (metrics + comparisons) from the \
experiments/results sections. Return JSON:
{"metrics": [{"name": str, "value": number|null, "value_text": str|null, \
"unit": str, "status": "reported"|"not_reported"|"not_applicable"|"unclear", \
"agg_type": "mean"|"best"|"max"|"worst"|"per_case"|"unknown", \
"condition": {dataset, pattern, pitch, wavelength, NA, technology, hardware, \
...}, "baseline": str, "source_evidence_id": str, "source_page": str, \
"source_section": str}], \
"comparisons": [{"metric": str, "condition": {...}, "baseline": str, \
"proposed": str, "improvement": str, \
"comparison_validity": "comparable"|"partially_comparable"|"not_comparable", \
"source_evidence_id": str}]}
Rules:
- status must be exactly one of the enum; if a number appears only in a figure \
and cannot be read reliably, use "unclear".
- Every "reported" metric MUST carry value/unit, its experimental condition, \
and source_evidence_id; keep baselines/ablations too, not just the best number.
- Do not fabricate percentages from qualitative statements."""
    return _normalize_l2_results(_chat(llm, "l2r", body, doc))


def _normalize_l2_results(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "metrics": [_normalize_metric(m) for m in (raw.get("metrics") or [])
                    if isinstance(m, dict)],
        "comparisons": [_normalize_comparison(c) for c in (raw.get("comparisons") or [])
                        if isinstance(c, dict)],
    }


def _normalize_metric(m: dict[str, Any]) -> dict[str, Any]:
    status = m.get("status") if m.get("status") in _METRIC_STATUSES else "unclear"
    val = m.get("value")
    val_text = m.get("value_text")
    if status == "reported" and val is None and not (val_text or "").strip():
        status = "not_reported"  # reported-without-value cannot stand
    return {
        "name": m.get("name") or "",
        "value": val if val is not None else None,
        "value_text": val_text,
        "unit": m.get("unit"),
        "status": status,
        "agg_type": m.get("agg_type") if m.get("agg_type") in (
            "mean", "best", "max", "worst", "per_case", "unknown") else None,
        "condition": m.get("condition") or {},
        "baseline": m.get("baseline"),
        "source_evidence_id": m.get("source_evidence_id"),
        "source_page": m.get("source_page"),
        "source_section": m.get("source_section"),
    }


def _normalize_comparison(c: dict[str, Any]) -> dict[str, Any]:
    validity = c.get("comparison_validity")
    if validity not in ("comparable", "partially_comparable", "not_comparable"):
        validity = "not_comparable"
    return {
        "metric": c.get("metric"),
        "condition": c.get("condition") or {},
        "baseline": c.get("baseline"),
        "proposed": c.get("proposed"),
        "improvement": c.get("improvement"),
        "comparison_validity": validity,
        "source_evidence_id": c.get("source_evidence_id"),
    }


# ---------------------------------------------------------------------------
# L3 claims + evidence
# ---------------------------------------------------------------------------

def extract_l3(llm, doc) -> dict[str, Any]:
    body = """Extract claims and their supporting evidence. Return JSON:
{"claims": [{"claim": str, "claim_type": "definition"|"methodological"|\
"quantitative"|"comparative"|"causal"|"limitation"|"conclusion", \
"strength": "A"|"B"|"C"|"D", "supporting_evidence_ids": [str]}], \
"evidence": [{"section": str, "subsection": str, "page": str, \
"source_text": str, "claim": str, "evidence_type": "definition"|\
"methodological_statement"|"observation"|"experimental_result"|"comparison"|\
"limitation"|"causal_claim"|"quantitative_result", "metric_refs": [str], \
"formula_refs": [str]}]}
- source_text MUST be verbatim from the paper (quote it exactly).
- Include the page/section when available — traceability is required.
- Do not save irrelevant background sentences as evidence."""
    return _normalize_l3(_chat(llm, "l3", body, doc))


def _normalize_l3(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "claims": [_normalize_claim(c) for c in (raw.get("claims") or [])
                   if isinstance(c, dict)],
        "evidence": [_normalize_evidence(e) for e in (raw.get("evidence") or [])
                     if isinstance(e, dict)],
    }


def _normalize_claim(c: dict[str, Any]) -> dict[str, Any]:
    ctype = c.get("claim_type") if c.get("claim_type") in _CLAIM_TYPES else "conclusion"
    strength = c.get("strength") if c.get("strength") in _STRENGTHS else None
    return {
        "claim": (c.get("claim") or "").strip(),
        "claim_type": ctype,
        "strength": strength,
        "supporting_evidence_ids": [str(x) for x in (c.get("supporting_evidence_ids") or [])],
    }


def _normalize_evidence(e: dict[str, Any]) -> dict[str, Any]:
    etype = e.get("evidence_type")
    if etype not in _EVIDENCE_TYPES:
        etype = None
    return {
        "section": e.get("section"),
        "subsection": e.get("subsection"),
        "page": e.get("page"),
        "source_text": (e.get("source_text") or "").strip(),
        "claim": e.get("claim"),
        "evidence_type": etype,
        "metric_refs": [str(x) for x in (e.get("metric_refs") or [])],
        "formula_refs": [str(x) for x in (e.get("formula_refs") or [])],
    }


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------

def extract_formulas(llm, doc) -> dict[str, Any]:
    body = """Extract the paper's formulas. Return JSON array of:
{"formula_latex": str, "formula_role": "objective"|"loss"|"forward_model"|\
"constraint"|"regularization"|"metric"|"physical_model"|"evaluation"|"network"|\
"update_rule", "semantic_description": str, "section": str, "page": str, \
"variables": [{"symbol": str, "meaning": str, "unit": str}], \
"application": str, "source_evidence_id": str, \
"reusability": {"directly_reusable": bool, "requires_context": bool}}
- Scan numbered equations, display math, loss/objective definitions, \
physical models, metrics and update rules.
- A variable whose meaning cannot be confirmed is marked "unclear" — do not guess."""
    return _normalize_formulas(llm.chat_json_list(_SYSTEM_PROMPT, _layer(
        "formulas", body, doc)))


def _normalize_formulas(raw: list[Any]) -> dict[str, Any]:
    out = []
    for f in raw or []:
        if not isinstance(f, dict):
            continue
        role = f.get("formula_role") if f.get("formula_role") in _FORMULA_ROLES else None
        vars_ = []
        for v in (f.get("variables") or []):
            if not isinstance(v, dict) or not v.get("symbol"):
                continue
            vars_.append({
                "symbol": v["symbol"],
                "meaning": v.get("meaning") or "unclear",
                "unit": v.get("unit"),
            })
        reus = f.get("reusability") or {}
        out.append({
            "formula_latex": (f.get("formula_latex") or "").strip(),
            "formula_role": role,
            "semantic_description": f.get("semantic_description"),
            "section": f.get("section"),
            "page": f.get("page"),
            "variables": vars_,
            "application": f.get("application"),
            "source_evidence_id": f.get("source_evidence_id"),
            "reusability": {
                "directly_reusable": bool(reus.get("directly_reusable")),
                "requires_context": bool(reus.get("requires_context")),
            },
        })
    return {"formulas": out}


# ---------------------------------------------------------------------------
# citation graph
# ---------------------------------------------------------------------------

def extract_citation_graph(llm, doc) -> dict[str, Any]:
    body = """Extract citation graph edges ONLY when reliably identifiable. \
Return JSON array of:
{"target_citation_key": str|null, "target_title": str|null, \
"relation": "cites"|"extends"|"improves"|"compares_with"|"uses"|"criticizes"|\
"builds_on"|"same_method_family", "confidence": number|null, \
"evidence_id": str|null}
- A mere bibliography reference is "cites" by default. Do NOT infer \
"improves"/"extends"/"compares_with" from a citation alone — the text must \
explicitly say the relation."""
    return _normalize_graph(llm.chat_json_list(_SYSTEM_PROMPT, _layer(
        "graph", body, doc)))


def _normalize_graph(raw: list[Any]) -> dict[str, Any]:
    out = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        rel = g.get("relation") if g.get("relation") in _GRAPH_RELATIONS else "cites"
        out.append({
            "target_citation_key": g.get("target_citation_key") or g.get("target_key"),
            "target_title": g.get("target_title"),
            "relation": rel,
            "confidence": g.get("confidence"),
            "evidence_id": g.get("evidence_id"),
        })
    return {"citation_graph": out}
