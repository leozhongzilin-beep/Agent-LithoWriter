"""Smoke tests for the writing agent pipeline using a mock LLM client.

Runs the full pipeline (plan -> write -> review -> finalize) against a fake
DeepSeek client so the tests work without an API key or network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from write_agent.config import load_config
from write_agent.pipeline import Pipeline


class MockClient:
    """Duck-typed replacement for DeepSeekClient that returns canned JSON."""

    def __init__(self):
        self.calls = []

    def chat(self, system, user, temperature=None, max_tokens=None, stop=None):
        self.calls.append(("chat", system[:60], user[:80]))
        # Inspect prompt markers to decide what to return.
        if "BEGIN FILE:" in user:
            # Fixer response: rewrite section 1
            return _mock_fix_response()
        if "five-sentence abstract" in user:
            return _MockResult("We prove that our method converges and reduces error by 15%.")
        if "Run the final scientific writing quality audit" in user:
            return _MockResult('{"issues": [], "passes_clean": [true,true,true,true,true], "overall": "clean"}')
        if "You are reviewing an academic paper" in user:
            # Reviewer response (zero-context round)
            return _MockResult(json.dumps({
                "score": 7.0,
                "summary": "Solid draft with a clear contribution.",
                "strengths": ["clear claims", "good structure"],
                "weaknesses": [
                    {"severity": "MAJOR", "issue": "Related work needs more depth",
                     "fix": "Expand related work", "location": "2_related_work.tex"}
                ],
                "verdict": "ready",
            }))
        return _MockResult("Draft section content for the requested section.")

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        self.calls.append(("chat_json", system[:60], user[:80]))
        if "core claims" in user:
            return {
                "one_sentence_contribution": "We show method A improves benchmark B by 15%.",
                "title": "A New Method for Benchmark B",
                "claims": [
                    {"claim": "Method A improves benchmark B by 15%",
                     "evidence": "exp1 on benchmark B",
                     "status": "supported", "section": "4"},
                ],
                "key_weaknesses": ["no ablation yet"],
                "suggested_framing": "position against prior methods",
            }
        if "building the paper outline" in user:
            return _mock_outline()
        return {}

    def chat_json_list(self, system, user, temperature=None, max_tokens=None):
        return []


class _MockResult:
    def __init__(self, text):
        self.text = text
        self.usage_input = 0
        self.usage_output = 0
        self.model = "mock"


def _mock_outline():
    return {
        "paper_type": "empirical",
        "sections": [
            {"id": "0", "title": "Abstract", "filename": "0_abstract.tex",
             "purpose": "summary", "key_points": [], "target_pages": 0.3},
            {"id": "1", "title": "Introduction", "filename": "1_introduction.tex",
             "purpose": "motivation and contributions",
             "key_points": ["hook", "gap", "approach", "contributions"],
             "citations_hint": [], "target_pages": 1.5},
            {"id": "2", "title": "Related Work", "filename": "2_related_work.tex",
             "purpose": "positioning", "key_points": ["synthesize categories"],
             "citations_hint": [], "target_pages": 1.0},
            {"id": "3", "title": "Method", "filename": "3_method.tex",
             "purpose": "approach", "key_points": ["formulation", "algorithm"],
             "citations_hint": [], "target_pages": 2.0},
            {"id": "4", "title": "Experiments", "filename": "4_experiments.tex",
             "purpose": "results", "key_points": ["setup", "main results", "ablation"],
             "citations_hint": [], "target_pages": 3.0},
            {"id": "5", "title": "Conclusion", "filename": "5_conclusion.tex",
             "purpose": "wrap up", "key_points": ["contributions", "limitations", "future"],
             "citations_hint": [], "target_pages": 0.5},
        ],
        "figure_plan": [
            {"id": "fig1", "type": "plot", "description": "main result comparison",
             "data_source": "exp1"}
        ],
        "citation_plan": {"intro": [], "related": [], "method": []},
    }


def _mock_fix_response():
    parts = []
    for fname in ["0_abstract.tex", "1_introduction.tex", "2_related_work.tex",
                  "3_method.tex", "4_experiments.tex", "5_conclusion.tex"]:
        parts.append(f"===== BEGIN FILE: {fname} =====\n"
                     f"% revised {fname}\nRevised content for {fname}.\n"
                     f"===== END FILE: {fname} =====")
    return _MockResult("\n".join(parts))


def test_full_pipeline(tmp_dir: Path) -> None:
    cfg = load_config()
    cfg.data["pipeline"]["output_dir"] = str(tmp_dir)
    cfg.data["model"]["name"] = "mock"
    cfg.data["review"]["max_rounds"] = 1
    cfg.data["review"]["min_score"] = 6.0
    cfg.data["write"]["dblp_verify"] = False  # no network in tests

    # Monkeypatch the client on the pipeline
    pipeline = Pipeline(cfg, verbose=True, client=MockClient())  # type: ignore[arg-type]

    report = pipeline.run(source="Test topic about method A and benchmark B",
                          max_rounds=1)

    assert "error" not in report, f"Pipeline errored: {report.get('error')}"
    assert report["plan"]["title"]
    paper = Path(tmp_dir) / "paper"
    assert (paper / "main.tex").exists(), "main.tex missing"
    assert (paper / "PAPER_PLAN.md").exists(), "PAPER_PLAN.md missing"
    sections = (paper / "sections")
    assert sections.exists()
    files = sorted(p.name for p in sections.glob("*.tex"))
    assert len(files) >= 5, f"Expected >=5 sections, got {files}"
    assert (paper / "PIPELINE_REPORT.md").exists()
    print(f"PASS: full pipeline smoke test. Sections: {files}")


def test_fix_parser(tmp_dir: Path) -> None:
    from write_agent.phases.review import _parse_fixed_sections
    text = """
===== BEGIN FILE: 1_introduction.tex =====
\section{Introduction}
Fixed intro.
===== END FILE: 1_introduction.tex =====
===== BEGIN FILE: 3_method.tex =====
\section{Method}
Fixed method.
===== END FILE: 3_method.tex =====
"""
    files = _parse_fixed_sections(text)
    assert set(files.keys()) == {"1_introduction.tex", "3_method.tex"}, files
    assert "Fixed intro" in files["1_introduction.tex"]
    print("PASS: fix parser")


def test_json_extract(tmp_dir: Path) -> None:
    from write_agent.llm import extract_json, extract_json_array
    obj = extract_json('```json\n{"a": 1}\n```')
    assert obj == {"a": 1}, obj
    arr = extract_json_array('Here is the list:\n```json\n[{"x": 1}]\n```')
    assert arr == [{"x": 1}], arr
    obj2 = extract_json('prefix text {"nested": {"k": [1,2,3]}} suffix')
    assert obj2 == {"nested": {"k": [1, 2, 3]}}, obj2
    print("PASS: json extraction")


def test_bib_write(tmp_dir: Path) -> None:
    from write_agent.citation import VerifiedEntry, write_bibliography
    e1 = VerifiedEntry(key="vaswani2017attention", bibtex="@article{vaswani2017attention,\n  title={Attention Is All You Need},\n}")
    e2 = VerifiedEntry(key="vaswani2017attention", bibtex="@article{vaswani2017attention,\n  title={DUPLICATE},\n}")
    n = write_bibliography([e1, e2], Path(tmp_dir) / "references.bib")
    assert n == 1, f"Expected 1 deduped entry, got {n}"
    text = (Path(tmp_dir) / "references.bib").read_text()
    assert "DUPLICATE" not in text
    print("PASS: bib dedup")


def test_make_key(tmp_dir: Path) -> None:
    from write_agent.citation import make_key
    k = make_key("Attention Is All You Need", "2017", "Vaswani, Ashish")
    assert k == "vaswani2017attention", k
    print("PASS: bib key generation")


def main() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_json_extract(tmp)
        test_fix_parser(tmp)
        test_bib_write(tmp)
        test_make_key(tmp)
        test_full_pipeline(tmp)
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
