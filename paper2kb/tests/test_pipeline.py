"""End-to-end pipeline tests: source -> package -> kb validation -> kb add."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from paper2kb.pipeline import process_paper
from paper2kb.validate import human_review_triggers, validate

# make the literature_kb `kb` package importable
_KB = Path(__file__).resolve().parent.parent.parent / "literature_kb"
sys.path.insert(0, str(_KB))


@pytest.fixture(autouse=True)
def _offline_metadata(monkeypatch):
    """Stub the live Crossref call so tests are deterministic and offline.

    Returns a year-only record so the KB's paper_id auto-assign works; title
    falls through to the document's title hint.
    """
    from paper2kb.metadata import BibRecord

    monkeypatch.setattr(
        "paper2kb.metadata.resolve_metadata",
        lambda doi=None, title=None, **kw: BibRecord(year=2024),
    )


class MockLLM:
    """Canned per-layer responses so the full pipeline runs offline."""

    def __init__(self, responses):
        self.responses = responses

    def _dispatch(self, user):
        m = re.search(r"LAYER: (\w+)", user)
        return self.responses[m.group(1) if m else "?"]

    def chat_json(self, system, user):
        return self._dispatch(user)

    def chat_json_list(self, system, user):
        return self._dispatch(user)


def _mock_llm():
    return MockLLM({
        "l0": {"one_line_description": "A KAN predictor for fast mask synthesis.",
               "keywords": ["KAN", "ILT"], "domain_tags": ["ILT"],
               "method_tags": ["KAN"]},
        "l1": {"abstract": "We propose a KAN predictor.",
               "research_problem": "ILT is slow.", "research_gap": "iterative.",
               "main_idea": "learn the inverse mapping.", "method_summary": "KAN predictor.",
               "main_contributions": ["KAN ILT"], "innovation": "direct",
               "key_findings_summary": "TAT reduced.", "limitations": ["one pattern"],
               "datasets_summary": "MetalSet", "methods_summary": "KAN",
               "recommended_use": {"method": "strong", "related_work": "moderate"}},
        "l2m": {"method_card": {"method_name": "KAN-ILT", "method_family": "KAN",
                                "task": "mask optimization", "input": "target",
                                "output": "mask", "architecture": "KAN",
                                "loss_function": "mse", "iterative_or_direct": "direct"},
                "system_context": {"wavelength": "193", "NA": "1.35"}},
        "l2r": {"metrics": [{
                    "name": "EPE", "value": 2.1, "unit": "nm", "status": "reported",
                    "condition": {"dataset": "MetalSet", "pitch": 45},
                    "source_evidence_id": "e1", "source_page": "5", "source_section": "IV"}],
                "comparisons": []},
        "l3": {"claims": [{"claim": "The method reduces TAT.", "claim_type": "comparative",
                           "strength": "B", "supporting_evidence_ids": ["e1"]}],
               "evidence": [{"section": "IV", "page": "5",
                             "source_text": "The KAN predictor reduces turnaround time.",
                             "claim": "reduces TAT.", "evidence_type": "experimental_result"}]},
        "formulas": [{"formula_latex": r"Z = \sigma(H M)", "formula_role": "forward_model",
                      "semantic_description": "litho forward model",
                      "variables": [{"symbol": "Z"}, {"symbol": "M", "meaning": "mask"}]}],
        "graph": [{"target_citation_key": "Zhang2024ILT"}],
    })


_SOURCE = (
    "# KAN-based Mask Optimization for ILT\n\n"
    "We propose a KAN predictor for fast mask synthesis.\n\n"
    "## Method\n\n"
    "A Kolmogorov-Arnold network maps the target to a mask directly.\n\n"
    "## Experiments\n\n"
    "The KAN predictor reduces turnaround time on MetalSet.\n"
)


def _write_source(tmp_path):
    src = tmp_path / "paper.md"
    src.write_text(_SOURCE, encoding="utf-8")
    return src


def test_pipeline_emits_ingestible_package(tmp_path):
    from kb.importtool import import_package
    from kb.store import KBStore

    src = _write_source(tmp_path)
    pkg = process_paper(src, llm=_mock_llm())

    # 1. canonical shape + validation gates pass
    assert pkg["package_spec_version"] == "1.0"
    assert pkg["processor"]["name"] == "paper_to_literature_kb"
    report = pkg["validation_report"]
    assert report["pass"] is True, report["errors"]
    assert pkg["paper"]["L0"]["title"] == "KAN-based Mask Optimization for ILT"
    assert pkg["paper"]["L2"]["metrics"][0]["status"] == "reported"

    # 2. the KB's OWN gates accept it (no duplicated validation)
    from kb.package import validate_package
    errors, _ = validate_package(pkg)
    assert errors == []

    # 3. it actually ingests via kb.add
    kbs = KBStore(tmp_path / "kbdata")
    kbs.init()
    res = import_package(kbs, pkg)
    assert res.decision == "INSERTED"
    assert kbs.get_paper(res.paper_id)["title"] == "KAN-based Mask Optimization for ILT"
    assert kbs.table_counts()["paper_metrics"] == 1


def test_pipeline_archives_source_hash_and_pointer(tmp_path):
    src = _write_source(tmp_path)
    pkg = process_paper(src, llm=_mock_llm())
    assert pkg["source"]["type"] == "md"
    assert pkg["source"]["hash"].startswith("sha256:")
    assert pkg["paper"]["L4"]["fulltext_pointer"] == str(src.resolve())


def test_validation_report_flags_human_review(tmp_path):
    src = _write_source(tmp_path)
    pkg = process_paper(src, llm=_mock_llm())
    report = validate(pkg)
    assert report["pass"] is True

    # inject a figure-estimated metric -> human review trigger fires
    pkg["paper"]["L2"]["metrics"].append(
        {"name": "CD", "value": None, "status": "unclear", "condition": {}})
    triggers = human_review_triggers(pkg)
    assert any("CD" in t for t in triggers)


def test_emit_leaves_paper_id_and_citation_key_for_kb(tmp_path):
    src = _write_source(tmp_path)
    pkg = process_paper(src, llm=_mock_llm())
    assert pkg["paper"]["L0"]["paper_id"] == ""
    assert pkg["paper"]["L0"]["citation_key"] == ""
