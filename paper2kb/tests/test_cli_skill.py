"""CLI smoke tests for paper2kb (offline via mock LLM + metadata stub)."""

from __future__ import annotations

import json
import re

import pytest
from paper2kb.cli import main
from paper2kb.metadata import BibRecord

_SOURCE = (
    "# KAN-based Mask Optimization for ILT\n\n"
    "We propose a KAN predictor.\n\n"
    "## Experiments\n\n"
    "The predictor reduces turnaround time on MetalSet.\n"
)


class MockLLM:
    def __init__(self, responses):
        self.responses = responses

    def _dispatch(self, user):
        m = re.search(r"LAYER: (\w+)", user)
        return self.responses[m.group(1) if m else "?"]

    def chat_json(self, system, user):
        return self._dispatch(user)

    def chat_json_list(self, system, user):
        return self._dispatch(user)


def _mock_llm(*a, **k):
    return MockLLM({
        "l0": {"one_line_description": "A KAN predictor.",
               "keywords": ["KAN"], "domain_tags": ["ILT"], "method_tags": ["KAN"]},
        "l1": {"abstract": "We propose a KAN predictor.", "research_problem": "slow.",
               "research_gap": "iterative.", "main_idea": "learn.", "method_summary": "KAN.",
               "main_contributions": ["c"], "innovation": "direct",
               "key_findings_summary": "fast", "limitations": ["one"],
               "datasets_summary": "MetalSet", "methods_summary": "KAN",
               "recommended_use": {"method": "strong"}},
        "l2m": {"method_card": {"method_name": "KAN-ILT", "method_family": "KAN",
                                "task": "mask opt", "iterative_or_direct": "direct"},
                "system_context": {}},
        "l2r": {"metrics": [{"name": "TAT", "value": None, "status": "not_reported"}],
                "comparisons": []},
        "l3": {"claims": [], "evidence": []},
        "formulas": [], "graph": [],
    })


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("paper2kb.pipeline.make_llm", _mock_llm)
    monkeypatch.setattr(
        "paper2kb.metadata.resolve_metadata",
        lambda doi=None, title=None, **kw: BibRecord(year=2024),
    )


def test_cli_writes_package(tmp_path, capsys):
    src = tmp_path / "paper.md"
    src.write_text(_SOURCE, encoding="utf-8")
    out = tmp_path / "package.json"
    rc = main([str(src), "--out", str(out)])
    assert rc == 0
    pkg = json.loads(out.read_text(encoding="utf-8"))
    assert pkg["paper"]["L0"]["title"] == "KAN-based Mask Optimization for ILT"
    assert pkg["paper"]["L0"]["year"] == 2024
    assert pkg["validation_report"]["pass"] is True


def test_cli_missing_key_dies(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    def boom(*a, **k):
        raise OSError("DEEPSEEK_API_KEY is not set")

    monkeypatch.setattr("paper2kb.pipeline.make_llm", boom)
    with pytest.raises(SystemExit):
        main(["whatever.md"])
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
