"""Tests for CitationResolver's KB-first resolution path."""
from __future__ import annotations

from dataclasses import dataclass

from write_agent.citation import CitationResolver
from write_agent.config import load_config


@dataclass(frozen=True)
class _FakeKbResolved:
    citation_key: str
    bibtex: str
    title: str
    year: str
    venue: str
    in_text: str


class _FakeKb:
    """A fake KbProvider: no discover_cards, configurable resolve_hint."""

    def __init__(self, hits):
        self.hits = hits

    def resolve_hint(self, hint):
        return self.hits

    def discover_cards(self, topic, *, max_tokens, limit):
        return []


_KB_BIB = "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}"


def _resolver(kb):
    cfg = load_config()
    cfg.data["write"]["dblp_verify"] = False  # keep fall-through offline + deterministic
    return CitationResolver(cfg, timeout=5, kb=kb)


def test_kb_hit_returns_kb_entry():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.verified
    assert e.source == "KB"
    assert e.key == "zhang2024deepilt"          # internal bibtex key, not CamelCase
    assert e.bibtex == _KB_BIB                   # verbatim
    assert e.title == "Deep Learning for Inverse Lithography"


def test_exact_citation_key_hit_bypasses_title_gate():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "A Completely Different Title", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Zhang2024DeepLearning")
    assert e is not None and e.source == "KB"


def test_title_mismatch_falls_through_to_unverified():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "A Completely Different Title", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")   # hint != key, title doesn't match
    assert e is not None and e.source == "UNVERIFIED"


def test_missing_bibtex_falls_through():
    hit = _FakeKbResolved("Zhang2024DeepLearning", "",
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.source == "UNVERIFIED"


def test_malformed_bibtex_falls_through():
    hit = _FakeKbResolved("Zhang2024DeepLearning", "not a bibtex",
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.source == "UNVERIFIED"


def test_kb_miss_falls_through_to_unverified():
    r = _resolver(_FakeKb([]))
    e = r.resolve_query("Some Totally Unknown Paper Title")
    assert e is not None and e.source == "UNVERIFIED"


def test_rewritten_title_with_matching_acronym_resolves():
    # The plan LLM expanded "GAN-OPC" differently but kept the acronym head.
    hit = _FakeKbResolved("Yang2020GANOPC", _KB_BIB,
                          "GAN-OPC: Mask Optimization with Lithography-guided GAN",
                          "2020", "TCAD", "(Yang et al., 2020)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("GAN-OPC: Generative Adversarial Networks for Optical Proximity Correction")
    assert e is not None and e.verified and e.source == "KB"


def test_rewritten_title_with_distinctive_year_resolves():
    # ICCAD benchmark: the year "2013" + shared terms bridge the paraphrase.
    hit = _FakeKbResolved("Banerjee2013ICCAD2013", _KB_BIB,
                          "ICCAD-2013 CAD contest in mask optimization and benchmark suite",
                          "2013", "ICCAD", "(Banerjee et al., 2013)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("ICCAD 2013 benchmark for lithography")
    assert e is not None and e.verified and e.source == "KB"


def test_generic_phrase_is_not_cross_matched():
    # A generic 3-word phrase that is a proper subset of a title must NOT
    # attach that paper (it refers to a different, unindexed paper).
    hit = _FakeKbResolved("Huang2025BlockInverse", _KB_BIB,
                          "Block-based inverse lithography technology with adaptive level-set",
                          "2025", "TCAD", "(Huang et al., 2025)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Inverse Lithography Technology")
    assert e is not None and e.source == "UNVERIFIED"
