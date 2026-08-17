from __future__ import annotations

from types import SimpleNamespace

from write_agent.llm import LLMResult
from write_agent.phases.write import _parse_updated_sections, apply_evidence_update


def test_parse_updated_sections_accepts_paths_optional_end_name_and_fences():
    response = r"""
Some harmless leading text.
===== BEGIN FILE: sections/0_abstract.tex =====
```latex
\begin{abstract}
Updated result.
\end{abstract}
```
===== END FILE =====
===== begin file: 1_introduction.tex =====
\section{Introduction}
Updated introduction.
===== end file: 1_introduction.tex =====
"""

    files = _parse_updated_sections(response)

    assert set(files) == {"0_abstract.tex", "1_introduction.tex"}
    assert files["0_abstract.tex"].startswith(r"\begin{abstract}")
    assert files["1_introduction.tex"].startswith(r"\section{Introduction}")


def test_apply_evidence_update_retries_and_writes_only_existing_sections(tmp_path):
    sections = tmp_path / "sections"
    sections.mkdir()
    abstract = sections / "0_abstract.tex"
    introduction = sections / "1_introduction.tex"
    abstract.write_text("old abstract\n", encoding="utf-8")
    introduction.write_text("old introduction\n", encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.responses = [
                LLMResult(text="I updated the evidence discussion."),
                LLMResult(text=r"""
===== BEGIN FILE: sections/0_abstract.tex =====
```latex
\begin{abstract}
New five-seed result.
\end{abstract}
```
===== END FILE =====
===== BEGIN FILE: main.tex =====
must not be written
===== END FILE: main.tex =====
"""),
            ]

        def chat(self, **kwargs):
            self.calls.append(kwargs)
            return self.responses.pop(0)

    client = FakeClient()
    config = SimpleNamespace(venue="ICLR", language="English")

    changed = apply_evidence_update(client, config, tmp_path, "five-seed evidence")

    assert changed == ["0_abstract.tex"]
    assert "New five-seed result" in abstract.read_text(encoding="utf-8")
    assert introduction.read_text(encoding="utf-8") == "old introduction\n"
    assert len(client.calls) == 2
    assert client.calls[1]["temperature"] == 0.0
    assert (tmp_path / "EVIDENCE_UPDATE_RESPONSE_ATTEMPT_1.txt").exists()
    assert (tmp_path / "EVIDENCE_UPDATE_RESPONSE_ATTEMPT_2.txt").exists()


def test_apply_evidence_update_retries_when_response_anchor_is_missing(tmp_path):
    sections = tmp_path / "sections"
    sections.mkdir()
    abstract = sections / "0_abstract.tex"
    abstract.write_text("Old unsupported width claim.\n", encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.responses = [
                LLMResult(text=r"""
===== BEGIN FILE: 0_abstract.tex =====
Old unsupported width claim.
===== END FILE: 0_abstract.tex =====
"""),
                LLMResult(text=r"""
===== BEGIN FILE: 0_abstract.tex =====
The hc24 five-seed mean is 0.9540 versus 0.9445 for hc32; the predeclared
0.02 margin is not met, so the seed-averaged evidence does not support the
strong width claim. Under lower-is-better, the reference mean 0.9445 is lower
and better than the target mean 0.9540.
===== END FILE: 0_abstract.tex =====
"""),
            ]

        def chat(self, **kwargs):
            self.calls.append(kwargs)
            return self.responses.pop(0)

    response = {
        "request_id": "WR-EXP-TEST",
        "status": "COMPLETED",
        "aggregate_results": {
            "metrics": {"normalized_score": {"mean": 0.9540403953}}
        },
        "comparison": {
            "reference_normalized_score_mean": 0.9445,
            "claim_supported": False,
        },
    }
    client = FakeClient()
    config = SimpleNamespace(venue="ICLR", language="English")
    request = {
        "request_id": "WR-EXP-TEST",
        "question": "Is network width a genuine lever?",
        "origin": {"section": "Abstract"},
    }

    changed = apply_evidence_update(
        client,
        config,
        tmp_path,
        "new response",
        responses=[response],
        requests=[request],
    )

    assert changed == ["0_abstract.tex"]
    assert "0.9540" in abstract.read_text(encoding="utf-8")
    assert "does not support" in abstract.read_text(encoding="utf-8")
    assert len(client.calls) == 2
    assert "SEMANTIC RETRY" in client.calls[1]["user"]
