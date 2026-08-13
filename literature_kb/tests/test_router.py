"""Tests for the retrieval router (kb/router.py) — intent → RoutePlan."""

from __future__ import annotations

import pytest
from kb.router import INTENTS, InvalidIntent, route


def test_route_discovery_plan():
    plan = route("DISCOVERY")
    assert plan.mode == "DISCOVERY"
    assert plan.start_layer == "L0"
    assert "L1" in plan.escalation
    assert "bm25" in plan.strategy
    assert plan.budget > 0


def test_route_known_intents_start_at_expected_layer():
    expected = {
        "DISCOVERY": "L0",
        "CITATION": "L0",
        "TECHNICAL": "L1",
        "RESULT": "L2",
        "FORMULA": "FORMULA",
        "VERIFICATION": "L3",
        "COMPARISON": "L2",
    }
    for intent, layer in expected.items():
        assert route(intent).start_layer == layer, intent


def test_route_intent_is_case_insensitive():
    assert route("discovery").mode == "DISCOVERY"
    assert route("Formula").mode == "FORMULA"


def test_route_escalation_reaches_l4():
    assert "L4" in route("TECHNICAL").escalation
    assert "L4" in route("RESULT").escalation
    assert "L4" in route("VERIFICATION").escalation


def test_route_budget_override():
    plan = route("RESULT", max_tokens=3000)
    assert plan.budget == 3000
    assert route("RESULT").budget == 1500  # default unchanged


def test_route_unknown_intent_raises():
    with pytest.raises(InvalidIntent):
        route("TRANSLATE")


def test_all_intents_are_registered():
    for intent in INTENTS:
        assert route(intent).mode == intent
