"""Tests for RetrievalService.resolve_hint() (kb/retrieve.py)."""
from __future__ import annotations

from kb.importtool import import_package
from kb.retrieve import RetrievalService


def _seed(tmp_kb, make_package, **L0_overrides):
    pkg = make_package()
    pkg["paper"]["L0"].update(L0_overrides)
    pkg["citation_records"] = []
    return import_package(tmp_kb, pkg).paper_id


def test_resolve_hint_by_citation_key(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("Zhang2024DeepLearning")
    assert len(hits) == 1
    assert hits[0].citation_key == "Zhang2024DeepLearning"
    assert hits[0].title == "Deep Learning for Inverse Lithography"
    assert "@article{zhang2024deepilt" in hits[0].bibtex
    assert "Zhang" in hits[0].in_text


def test_resolve_hint_by_doi(tmp_kb, make_package):
    pid = _seed(tmp_kb, make_package, doi="10.1016/x1")
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("10.1016/x1")
    assert len(hits) == 1 and hits[0].paper_id == pid


def test_resolve_hint_by_title_search(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("deep learning for inverse lithography")
    assert hits and "lithography" in hits[0].title.lower()


def test_resolve_hint_missing_bibtex_is_empty_string(tmp_kb, make_package):
    pkg = make_package()
    del pkg["paper"]["L0"]["citation_cache"]
    pkg["citation_records"] = []
    import_package(tmp_kb, pkg)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("Zhang2024DeepLearning")
    assert hits and hits[0].bibtex == ""


def test_resolve_hint_empty_kb_returns_empty_list(tmp_kb):
    svc = RetrievalService(tmp_kb)
    assert svc.resolve_hint("anything") == []


def test_resolve_hint_recalls_by_leading_name(tmp_kb, make_package):
    """A rewritten subtitle keeps the acronym head; head recall surfaces it."""
    _seed(tmp_kb, make_package,
          title="GAN-OPC: Mask Optimization with Lithography-guided GAN")
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint(
        "GAN-OPC: Generative Adversarial Networks for Optical Proximity Correction"
    )
    assert hits and "GAN-OPC" in hits[0].title


def test_resolve_hint_recalls_via_or_join(tmp_kb, make_package):
    """Paraphrased hint shares only distinctive terms; OR-join recall finds it."""
    _seed(tmp_kb, make_package,
          title="ICCAD-2013 CAD contest in mask optimization and benchmark suite")
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("ICCAD 2013 benchmark for lithography")
    assert hits and "ICCAD" in hits[0].title
