"""Tests for L3 evidence search + claim verification (kb/evidence.py)."""

from __future__ import annotations

from kb.evidence import search_evidence, verify_claim
from kb.importtool import import_package


def test_search_evidence_by_token(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    hits = search_evidence(tmp_kb, "EPE")
    assert hits and hits[0].paper_id == res.paper_id
    assert hits[0].source_text  # verbatim source text preserved
    assert hits[0].section == "IV"


def test_search_evidence_restricts_to_paper_ids(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    assert search_evidence(tmp_kb, "EPE", paper_ids=["ILT_9999_999"]) == []
    hits = search_evidence(tmp_kb, "EPE", paper_ids=[res.paper_id])
    assert hits and hits[0].paper_id == res.paper_id


def test_verify_supported_with_strength(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    v = verify_claim(tmp_kb, "The method reduces turnaround time")
    assert v.verdict == "supported"
    assert v.strength == "B"  # stored claim strength, never invented
    assert v.evidence


def test_verify_unverified_when_no_evidence(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    v = verify_claim(tmp_kb, "quantum entanglement improves EPE")
    assert v.verdict == "unverified"
    assert v.strength is None


def test_verify_never_asserts_strength_without_claim(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    # matches evidence text but shares no token with any stored claim
    v = verify_claim(tmp_kb, "EPE of 2.1 nm")
    assert v.verdict == "supported"
    assert v.strength is None
