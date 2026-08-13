"""Tests for hybrid relevance scoring (kb/relevance.py)."""

from __future__ import annotations

from kb.relevance import (
    compose_score,
    concept_expansions,
    graph_centrality,
    metadata_match,
    normalize,
    recency,
)


def test_normalize_minmax():
    assert normalize([0.0, 0.5, 1.0]) == [0.0, 0.5, 1.0]
    assert normalize([2.0, 4.0]) == [0.0, 1.0]
    assert normalize([]) == []
    assert normalize([3.0]) == [1.0]  # single value -> top


def test_recency_clips_and_handles_none():
    assert recency(2024) == pytest_approx((2024 - 2000) / 30)
    assert recency(None) == 0.0
    assert recency(2015) == 0.5
    assert recency(2050) == 1.0  # clipped
    assert recency(1990) == 0.0  # clipped below


def test_metadata_match_all_satisfied():
    paper = {"paper_id": "ILT_2024_001", "year": 2024,
             "method_tags": ["KAN"], "venue": "IEEE TCAD"}
    filters = {"year_from": 2020, "method": "KAN", "venue": "ieee tcad"}
    assert metadata_match(filters, paper) == 1.0


def test_metadata_match_partial_and_none():
    paper = {"paper_id": "ILT_2024_001", "year": 2024,
             "method_tags": ["CNN"], "venue": "IEEE TCAD"}
    assert metadata_match({"method": "KAN"}, paper) == 0.0
    assert metadata_match({"year_from": 2030, "method": "CNN"}, paper) == 0.5
    assert metadata_match({}, paper) == 0.0


def test_domain_match_via_paper_id_prefix():
    paper = {"paper_id": "SMO_2025_017", "year": 2025}
    assert metadata_match({"domain": "smo"}, paper) == 1.0
    assert metadata_match({"domain": "ILT"}, paper) == 0.0


def test_graph_centrality():
    assert graph_centrality(0, 10) == 0.0
    assert graph_centrality(5, 10) == 0.5
    assert graph_centrality(3, 0) == 0.0  # no graph -> no centrality


def test_compose_score_default_weights():
    s = compose_score(bm25_norm=1.0, recency_val=0.0,
                      metadata_val=0.0, centrality_val=0.0)
    assert s == pytest_approx(0.55)
    s2 = compose_score(bm25_norm=1.0, recency_val=1.0,
                       metadata_val=1.0, centrality_val=1.0)
    assert s2 == pytest_approx(1.0)


def test_compose_score_hybrid_vector_term():
    # vector term present -> hybrid weights (bm25, vector, recency, metadata, centrality)
    s = compose_score(1.0, 0.0, 0.0, 0.0, vector_norm=0.0)
    assert s == pytest_approx(0.45)
    s2 = compose_score(0.0, 0.0, 0.0, 0.0, vector_norm=1.0)
    assert s2 == pytest_approx(0.20)
    s3 = compose_score(1.0, 1.0, 1.0, 1.0, vector_norm=1.0)
    assert s3 == pytest_approx(1.0)


def test_concept_expansions_appends_matching_aliases():
    alias_map = {
        "ai-ilt": ["AI-ILT", "deep-learning ILT"],
        "mask optimization": ["ILT"],
    }
    assert concept_expansions("ai-ilt runtime", alias_map) == ["AI-ILT", "deep-learning ILT"]
    assert concept_expansions("lithography", alias_map) == []  # no alias matches


def pytest_approx(x):
    import pytest
    return pytest.approx(x)
