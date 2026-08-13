"""Tests for per-layer LLM extraction + normalization (paper2kb/extractors.py)."""

from __future__ import annotations

import re

from paper2kb.extractors import (
    extract_citation_graph,
    extract_formulas,
    extract_l0,
    extract_l1,
    extract_l2_method,
    extract_l2_results,
    extract_l3,
)
from paper2kb.metadata import merge_metadata


class MockLLM:
    """Returns canned per-layer JSON, dispatched on the LAYER: marker."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses

    def _dispatch(self, user):
        m = re.search(r"LAYER: (\w+)", user)
        layer = m.group(1) if m else "?"
        return self.responses[layer]

    def chat_json(self, system, user):
        return self._dispatch(user)

    def chat_json_list(self, system, user):
        return self._dispatch(user)


def _doc(text="Some paper text."):
    from paper2kb.parser import ParsedDoc
    return ParsedDoc(source_type="md", full_text=text)


def test_extract_l0_merges_metadata():
    meta = merge_metadata(None, {"title": "A KAN Paper", "author": "Li, Ming"})
    llm = MockLLM({"l0": {"one_line_description": "A KAN predictor.",
                          "keywords": ["KAN"], "domain_tags": ["ILT"],
                          "method_tags": ["KAN"]}})
    out = extract_l0(llm, _doc(), meta)
    assert out["title"] == "A KAN Paper"
    assert out["one_line_description"] == "A KAN predictor."
    assert out["method_tags"] == ["KAN"]
    assert out["domain_tags"] == ["ILT"]


def test_extract_l2_results_coerces_bad_status():
    llm = MockLLM({"l2r": {"metrics": [
        {"name": "EPE", "value": 2.1, "unit": "nm",
         "status": "guessed", "source_evidence_id": "e1"},  # invalid status
        {"name": "Runtime", "status": "reported"},            # reported but no value
        {"name": "TAT", "status": "not_reported"},
    ], "comparisons": []}})
    out = extract_l2_results(llm, _doc())
    m0, m1, m2 = out["metrics"]
    assert m0["status"] == "unclear"      # invalid -> unclear, never fabricated
    assert m1["status"] == "not_reported"  # reported-without-value cannot stand
    assert m2["status"] == "not_reported"
    assert m0["value"] == 2.1


def test_extract_l2_results_keeps_condition_and_evidence():
    llm = MockLLM({"l2r": {"metrics": [
        {"name": "PVBand", "value": 3.2, "unit": "nm", "status": "reported",
         "condition": {"dataset": "MetalSet", "pitch": 45},
         "source_evidence_id": "e1", "source_page": "4"},
    ], "comparisons": []}})
    m = extract_l2_results(llm, _doc())["metrics"][0]
    assert m["condition"]["dataset"] == "MetalSet"
    assert m["source_evidence_id"] == "e1"
    assert m["source_page"] == "4"


def test_extract_l3_evidence_preserved():
    llm = MockLLM({"l3": {
        "claims": [{"claim": "The method reduces TAT.", "claim_type": "comparative",
                    "strength": "B", "supporting_evidence_ids": ["e1"]}],
        "evidence": [{"section": "IV", "page": "5",
                      "source_text": "The method reduces turnaround time.",
                      "claim": "reduces TAT.", "evidence_type": "experimental_result"}],
    }})
    out = extract_l3(llm, _doc())
    assert out["evidence"][0]["source_text"] == "The method reduces turnaround time."
    assert out["evidence"][0]["page"] == "5"
    assert out["claims"][0]["strength"] == "B"


def test_extract_formulas_marks_unknown_variable_meanings():
    llm = MockLLM({"formulas": [{
        "formula_latex": r"Z = \sigma(H M)",
        "formula_role": "forward_model",
        "semantic_description": "litho forward model",
        "variables": [{"symbol": "Z"}, {"symbol": "M", "meaning": "mask"}],
    }]})
    formulas = extract_formulas(llm, _doc())["formulas"]
    by_sym = {v["symbol"]: v["meaning"] for v in formulas[0]["variables"]}
    assert by_sym["M"] == "mask"
    assert by_sym["Z"] == "unclear"  # never guessed


def test_extract_citation_graph_defaults_to_cites():
    llm = MockLLM({"graph": [
        {"target_citation_key": "Zhang2024ILT", "relation": "related_to"},  # invalid
        {"target_title": "An Unknown Paper"},
    ]})
    edges = extract_citation_graph(llm, _doc())["citation_graph"]
    # an invalid relation is normalized to cites (Skill step 11 rule)
    assert edges[0]["relation"] == "cites"
    assert edges[0]["target_citation_key"] == "Zhang2024ILT"
    assert edges[1]["target_title"] == "An Unknown Paper"


def test_extract_l1_and_l2_method_shape():
    llm = MockLLM({
        "l1": {"abstract": "We propose X.", "research_problem": "ILT is slow.",
               "research_gap": "prior iterative", "main_idea": "learn it",
               "method_summary": "A predictor.", "main_contributions": ["c1"],
               "innovation": "direct", "key_findings_summary": "fast",
               "limitations": ["one pattern"], "datasets_summary": "MetalSet",
               "methods_summary": "ML",
               "recommended_use": {"method": "strong", "background": "bogus"}},
        "l2m": {"method_card": {"method_name": "KAN-ILT", "method_family": "KAN",
                                "task": "mask opt", "iterative_or_direct": "direct"},
                "system_context": {"NA": 1.35, "wavelength": 193}},
    })
    l1 = extract_l1(llm, _doc())
    assert l1["recommended_use"]["method"] == "strong"
    assert l1["recommended_use"].get("background") != "bogus"  # invalid tag dropped
    card = extract_l2_method(llm, _doc())["method_card"]
    assert card["method_name"] == "KAN-ILT"
    assert card["system_context"]["NA"] == 1.35
