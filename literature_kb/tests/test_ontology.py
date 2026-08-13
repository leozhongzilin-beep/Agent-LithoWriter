"""Tests for ontology seeding + concept expansion (kb/ontology.py)."""

from __future__ import annotations

from kb.importtool import import_package
from kb.ontology import alias_map, load_seed, seed, validate_seed
from kb.retrieve import RetrievalService


def _default_seed():
    return load_seed()  # bundled kb/seeds/*.yaml


def test_default_seed_is_valid():
    data = _default_seed()
    errors, _ = validate_seed(data)
    assert errors == []
    assert data["concepts"] and data["metrics"]


def test_validate_detects_orphan_parent():
    data = {
        "concepts": [{
            "concept_id": "x", "canonical_name": "X",
            "parent_concepts": ["ghost"],
        }],
        "metrics": [],
    }
    errors, _ = validate_seed(data)
    assert any("ghost" in e for e in errors)


def test_validate_detects_duplicate_concept_id():
    data = {
        "concepts": [
            {"concept_id": "x", "canonical_name": "X"},
            {"concept_id": "x", "canonical_name": "Y"},
        ],
        "metrics": [],
    }
    errors, _ = validate_seed(data)
    assert errors


def test_seed_is_idempotent(tmp_kb):
    data = _default_seed()
    first = seed(tmp_kb, data)
    second = seed(tmp_kb, data)
    assert first == second
    assert tmp_kb.table_counts()["concepts"] == len(data["concepts"])
    assert tmp_kb.table_counts()["metrics_ontology"] == len(data["metrics"])


def test_alias_map_resolves_children(tmp_kb):
    seed(tmp_kb, _default_seed())
    am = alias_map(tmp_kb)
    # "AI-ILT" expands to its canonical name + its children's canonical names
    assert "AI-ILT" in am["ai-ilt"]
    assert "Neural mask synthesis" in am["ai-ilt"]
    assert "Deep-learning ILT" in am["ai-ilt"]


def test_retrieve_activates_concept_expansion(tmp_kb, make_package):
    """A query using an alias finds papers whose title carries a child term."""
    pkg = make_package()
    pkg["paper"]["L0"]["title"] = "Neural Mask Synthesis for Inverse Lithography"
    pkg["paper"]["L0"]["doi"] = "10.1016/x9"
    pkg["citation_records"] = []
    res = import_package(tmp_kb, pkg)
    seed(tmp_kb, _default_seed())

    svc = RetrievalService(tmp_kb)
    rs = svc.retrieve("AI-ILT", "DISCOVERY")  # alias only — not in the title
    assert any(r.paper_id == res.paper_id for r in rs.results)
