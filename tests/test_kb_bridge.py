"""Tests for write_agent.kb_bridge — the optional KB provider."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "literature_kb"))

from kb.importtool import import_package  # noqa: E402
from kb.store import KBStore  # noqa: E402
from write_agent.config import load_config  # noqa: E402
from write_agent.kb_bridge import KbCard, KbResolved, build_kb_provider  # noqa: E402


def _make_package():
    return {
        "package_spec_version": "1.0",
        "processor": {"name": "t", "version": "0.1"},
        "source": {"path": "p.pdf", "hash": None, "type": "pdf"},
        "paper": {"L0": {
            "paper_id": "",
            "title": "Deep Learning for Inverse Lithography",
            "one_line_description": "A CNN-ILT method for mask optimization.",
            "authors_summary": "Zhang et al.",
            "year": 2024,
            "venue": "Optics and Lasers in Engineering",
            "article_type": "journal",
            "doi": "10.1016/x1",
            "url": None,
            "keywords": ["ILT"], "domain_tags": ["ILT"], "method_tags": ["CNN"],
            "bibliographic_record": {
                "authors": [{"family": "Zhang", "given": "Wei"}],
                "title": "Deep Learning for Inverse Lithography",
                "container_title": "Optics and Lasers in Engineering",
                "year": 2024, "doi": "10.1016/x1",
            },
            "citation_key": "",
            "citation_cache": {
                "bibtex": "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}",
            },
        }},
        "formulas": [], "citation_records": [], "citation_graph": [],
        "validation_report": {},
    }


def _seed_kb(root: Path) -> KBStore:
    store = KBStore(root)
    store.init()
    import_package(store, _make_package())
    return store


def test_build_kb_provider_none_without_kb_path():
    cfg = load_config()
    assert build_kb_provider(cfg) is None


def test_build_kb_provider_uninitialized_dir_returns_none(tmp_path):
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path / "empty")
    assert build_kb_provider(cfg) is None


def test_adapter_resolve_hint(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path)
    provider = build_kb_provider(cfg)
    assert provider is not None
    hits = provider.resolve_hint("Zhang2024DeepLearning")
    assert len(hits) == 1
    assert isinstance(hits[0], KbResolved)
    assert hits[0].citation_key == "Zhang2024DeepLearning"
    assert "@article{zhang2024deepilt" in hits[0].bibtex


def test_adapter_discover_cards(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path)
    provider = build_kb_provider(cfg)
    cards = provider.discover_cards("inverse lithography", max_tokens=800, limit=5)
    assert cards
    assert isinstance(cards[0], KbCard)
    assert "lithography" in cards[0].title.lower()
    assert cards[0].year == "2024"
