"""Write-phase related-work grounding tests (fake provider + MockLLM)."""
from __future__ import annotations

from dataclasses import dataclass

from write_agent.citation import CitationResolver
from write_agent.config import load_config
from write_agent.phases.write import (
    format_kb_cards,
    ground_related_work,
    related_work_topics,
)


@dataclass(frozen=True)
class _Card:
    citation_key: str
    title: str
    one_line: str
    year: str
    in_text: str


@dataclass(frozen=True)
class _Resolved:
    citation_key: str
    bibtex: str
    title: str
    year: str
    venue: str
    in_text: str


class _FakeKb:
    def __init__(self, cards):
        self.cards = cards

    def resolve_hint(self, hint):
        out = []
        for c in self.cards:
            if c.title.lower() in hint.lower():
                # internal BibTeX key is independent of the CamelCase citation_key
                bib = f"@article{{zhang2024deepilt,\n  title = {{{c.title}}},\n  year = {{{c.year}}}\n}}"
                out.append(_Resolved(c.citation_key, bib, c.title, c.year, "OLE", c.in_text))
        return out

    def discover_cards(self, topic, *, max_tokens, limit):
        return [c for c in self.cards][:limit]


def _cfg():
    cfg = load_config()
    cfg.data["write"]["dblp_verify"] = False  # keep grounding resolution offline
    return cfg


def test_related_work_topics_deduplicates():
    section = {"citations_hint": ["Title A", "Title A", ""],
               "key_points": ["synthesize", "Title A"]}
    topics = related_work_topics(section)
    assert topics == ["Title A", "synthesize"]


def test_format_kb_cards():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    out = format_kb_cards([(card, "zhang2024deepilt")])
    assert "[zhang2024deepilt]" in out
    assert "Deep Learning for Inverse Lithography" in out
    assert "A CNN-ILT method." in out
    assert format_kb_cards([]) == ""


def test_ground_related_work_enqueues_and_resolves():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    kb = _FakeKb([card])
    resolver = CitationResolver(_cfg(), timeout=5, kb=kb)
    section = {"title": "Related Work", "citations_hint": [], "key_points": ["inverse lithography"]}
    keys, entries, seen = [], [], set()
    block = ground_related_work(kb, section, _cfg(), resolver, keys, entries, seen)
    assert keys == ["zhang2024deepilt"]
    assert len(entries) == 1 and entries[0].source == "KB"
    assert "[zhang2024deepilt]" in block
    assert "Deep Learning for Inverse Lithography" in block


def test_ground_related_work_skips_already_seen():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    kb = _FakeKb([card])
    resolver = CitationResolver(_cfg(), timeout=5, kb=kb)
    section = {"title": "Related Work", "citations_hint": [], "key_points": ["x"]}
    keys, entries, seen = [], [], {card.title}
    block = ground_related_work(kb, section, _cfg(), resolver, keys, entries, seen)
    assert keys == [] and block == ""
