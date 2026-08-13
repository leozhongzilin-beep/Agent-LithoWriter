"""Tests for the retrieval return contract (kb/contract.py)."""

from __future__ import annotations

from kb.contract import (
    ResultItem,
    ResultSet,
    estimate_tokens,
    truncate_to_budget,
)


def test_estimate_tokens_uses_chars_per_4():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_truncate_keeps_highest_relevance_under_budget():
    items = [
        ResultItem(paper_id="ILT_2024_001", title="A", relevance=0.9,
                   key_fact="x" * 400),  # ~100 tokens
        ResultItem(paper_id="ILT_2024_002", title="B", relevance=0.8,
                   key_fact="x" * 400),  # ~100 tokens
        ResultItem(paper_id="ILT_2024_003", title="C", relevance=0.7,
                   key_fact="x" * 400),  # ~100 tokens
    ]
    kept, truncated = truncate_to_budget(items, budget=210)
    # first two fit (~200 tokens), the third must be dropped
    assert [i.paper_id for i in kept] == ["ILT_2024_001", "ILT_2024_002"]
    assert truncated is True


def test_truncate_no_truncation_when_budget_holds():
    items = [
        ResultItem(paper_id="ILT_2024_001", title="A", relevance=0.9,
                   key_fact="x" * 40),  # ~10 tokens
    ]
    kept, truncated = truncate_to_budget(items, budget=100)
    assert len(kept) == 1
    assert truncated is False


def test_truncate_empty_budget_returns_nothing():
    items = [
        ResultItem(paper_id="ILT_2024_001", title="A", relevance=0.9,
                   key_fact="x" * 40),
    ]
    kept, truncated = truncate_to_budget(items, budget=0)
    assert kept == []
    assert truncated is True


def test_resultset_serializes_to_contract_shape():
    rs = ResultSet(
        query="deep learning ILT",
        mode="DISCOVERY",
        results=[
            ResultItem(paper_id="ILT_2024_001", title="A", relevance=0.9,
                       why_relevant="matches", best_use="related work",
                       key_fact="learned mask optimization",
                       citation_key="Zhang2024ILT", citation="(Zhang et al., 2024)",
                       available_levels=["L0", "L1"], evidence_ids=["ILT_2024_001.ev001"]),
        ],
        next_action="escalate to L1",
        truncated=False,
    )
    d = rs.to_dict()
    assert d["mode"] == "DISCOVERY"
    assert d["next_action"] == "escalate to L1"
    assert d["results"][0]["citation_key"] == "Zhang2024ILT"
    assert d["results"][0]["evidence_ids"] == ["ILT_2024_001.ev001"]
