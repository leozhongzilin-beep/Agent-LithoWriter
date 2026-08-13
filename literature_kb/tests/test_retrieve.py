"""End-to-end tests for RetrievalService.retrieve() (kb/retrieve.py)."""

from __future__ import annotations

import pytest
from kb.contract import ResultSet
from kb.importtool import import_package
from kb.retrieve import RetrievalService
from kb.router import InvalidIntent


def _seed(tmp_kb, make_package, *, title="Deep Learning for Inverse Lithography",
          doi="10.1016/x1", metric="EPE"):
    pkg = make_package()
    L0 = pkg["paper"]["L0"]
    L0["title"] = title
    L0["doi"] = doi
    L0["year"] = 2024
    pkg["paper"]["L2"]["metrics"][0]["name"] = metric
    pkg["citation_records"] = []
    return import_package(tmp_kb, pkg).paper_id


def test_retrieve_discovery(tmp_kb, make_package):
    pid = _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("lithography", "DISCOVERY")
    assert isinstance(rs, ResultSet)
    assert rs.mode == "DISCOVERY"
    assert rs.results[0].paper_id == pid
    assert rs.next_action  # escalate hint present


def test_retrieve_technical_uses_l1(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("inverse lithography", "TECHNICAL")
    assert rs.results[0].key_fact  # L1 card summary as key fact
    assert "CNN" in rs.results[0].key_fact


def test_retrieve_result_uses_l2(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("EPE", "RESULT")
    assert rs.results[0].key_fact.startswith("EPE=")


def test_retrieve_citation_resolves_by_key(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("Zhang2024DeepLearning", "CITATION")
    assert rs.results and rs.results[0].citation_key == "Zhang2024DeepLearning"
    assert "Zhang" in rs.results[0].citation


def test_retrieve_formula(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("loss", "FORMULA")
    assert rs.results and rs.results[0].available_levels == ["FORMULA"]
    assert "loss" in rs.results[0].key_fact.lower()


def test_retrieve_verification(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("The method reduces turnaround time", "VERIFICATION")
    assert rs.results and "EPE" in rs.results[0].key_fact


def test_retrieve_l4_method_and_hint(tmp_kb, make_package):
    from kb import chunker

    res = import_package(tmp_kb, make_package())
    chunker.store_chunks(tmp_kb, res.paper_id, chunker.chunk_markdown(
        "# T\n\n## Method\n\nThe KAN model reduces turnaround time.\n",
        res.paper_id))
    svc = RetrievalService(tmp_kb)
    hits = svc.l4("turnaround")
    assert hits and hits[0].paper_id == res.paper_id
    assert hits[0].available_levels == ["L4"]
    # an L3 verification now hints the L4 escalation step
    rs = svc.retrieve("The KAN model reduces turnaround time", "VERIFICATION")
    assert rs.results and "L4" in rs.next_action


def test_retrieve_budget_truncation(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    tiny = svc.retrieve("lithography", "DISCOVERY", max_tokens=10)
    assert tiny.results == [] and tiny.truncated is True
    big = svc.retrieve("lithography", "DISCOVERY", max_tokens=10000)
    assert big.results and big.truncated is False


def test_retrieve_unknown_intent_raises(tmp_kb):
    with pytest.raises(InvalidIntent):
        RetrievalService(tmp_kb).retrieve("x", "TRANSLATE")


def test_service_convenience_methods(tmp_kb, make_package):
    pid = _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    card = svc.get_card(pid)
    assert card["research_problem"] == "ILT is expensive."
    structured = svc.structured(pid)
    assert structured["metrics"][0]["name"] == "EPE"
    v = svc.verify("The method reduces turnaround time")
    assert v.verdict == "supported"
    assert svc.formulas("loss")[0].formula_role == "loss"
    cit = svc.cite(pid, style_id="nature")
    assert cit["generated"] is True
