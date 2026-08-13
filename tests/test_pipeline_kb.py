"""Pipeline wiring for the optional KB provider."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "literature_kb"))

from write_agent.config import load_config
from write_agent.pipeline import Pipeline

from kb.importtool import import_package


def _seed_kb(root: Path) -> None:
    pkg = {
        "package_spec_version": "1.0",
        "processor": {"name": "t", "version": "0.1"},
        "source": {"path": "p.pdf", "hash": None, "type": "pdf"},
        "paper": {"L0": {
            "paper_id": "", "title": "Deep Learning for Inverse Lithography",
            "one_line_description": "A CNN-ILT method.", "authors_summary": "Zhang et al.",
            "year": 2024, "venue": "OLE", "article_type": "journal",
            "doi": "10.1016/x1", "url": None,
            "keywords": ["ILT"], "domain_tags": ["ILT"], "method_tags": ["CNN"],
            "bibliographic_record": {
                "authors": [{"family": "Zhang", "given": "Wei"}],
                "title": "Deep Learning for Inverse Lithography",
                "container_title": "OLE", "year": 2024, "doi": "10.1016/x1",
            },
            "citation_key": "",
            "citation_cache": {"bibtex": "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}"},
        }},
        "formulas": [], "citation_records": [], "citation_graph": [],
        "validation_report": {},
    }
    from kb.store import KBStore
    store = KBStore(root)
    store.init()
    import_package(store, pkg)


def _cfg():
    cfg = load_config()
    cfg.data["model"]["api_key"] = "test-key"  # Pipeline builds DeepSeekClient eagerly
    return cfg


def test_pipeline_no_kb_no_resolver():
    cfg = _cfg()
    cfg.data["write"]["dblp_verify"] = False
    p = Pipeline(cfg, verbose=False)
    assert p.citation_resolver is None


def test_pipeline_kb_only_builds_resolver(tmp_path):
    _seed_kb(tmp_path)
    cfg = _cfg()
    cfg.data["write"]["dblp_verify"] = False
    cfg.data["write"]["kb_path"] = str(tmp_path)
    p = Pipeline(cfg, verbose=False)
    assert p.citation_resolver is not None
    assert p.citation_resolver.kb is not None


# ---------------------------------------------------------------------------
# End-to-end: KB-only full pipeline (skip review), no network.
# ---------------------------------------------------------------------------

class _MockResult:
    def __init__(self, text):
        self.text = text
        self.usage_input = 0
        self.usage_output = 0
        self.model = "mock"


class _MockClient:
    """Duck-typed DeepSeekClient that records prompts and returns canned output."""

    def __init__(self):
        self.calls = []

    def chat(self, system, user, temperature=None, max_tokens=None, stop=None):
        self.calls.append(("chat", user))
        if "Run the final scientific writing quality audit" in user:
            return _MockResult(
                '{"issues": [], "passes_clean": [true,true,true,true,true,true,true,true,true,true,true,true,true], "overall": "clean"}'
            )
        return _MockResult("Draft section content.")

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        self.calls.append(("chat_json", user))
        if "core claims" in user:
            return {
                "one_sentence_contribution": "A CNN-ILT method improves mask optimization.",
                "title": "A New Method for Mask Optimization",
                "claims": [
                    {"claim": "CNN-ILT reduces turnaround time",
                     "evidence": "exp1", "status": "supported", "section": "4"},
                ],
                "key_weaknesses": ["no ablation"],
                "suggested_framing": "position against prior methods",
            }
        if "building the paper outline" in user:
            return _KB_OUTLINE
        return {}

    def chat_json_list(self, system, user, temperature=None, max_tokens=None):
        return []


_KB_HINT = "Deep Learning for Inverse Lithography (Zhang et al., 2024)"

_KB_OUTLINE = {
    "paper_type": "empirical",
    "sections": [
        {"id": "0", "title": "Abstract", "filename": "0_abstract.tex", "purpose": "summary", "key_points": [], "target_pages": 0.3},
        {"id": "1", "title": "Introduction", "filename": "1_introduction.tex", "purpose": "motivation", "key_points": ["hook"], "citations_hint": [], "target_pages": 1.5},
        {"id": "2", "title": "Related Work", "filename": "2_related_work.tex", "purpose": "positioning",
         "key_points": ["inverse lithography"], "citations_hint": [_KB_HINT], "target_pages": 1.0},
        {"id": "3", "title": "Method", "filename": "3_method.tex", "purpose": "approach", "key_points": ["formulation"], "citations_hint": [], "target_pages": 2.0},
        {"id": "4", "title": "Experiments", "filename": "4_experiments.tex", "purpose": "results", "key_points": ["setup"], "citations_hint": [], "target_pages": 3.0},
        {"id": "5", "title": "Conclusion", "filename": "5_conclusion.tex", "purpose": "wrap up", "key_points": ["contributions"], "citations_hint": [], "target_pages": 0.5},
    ],
    "figure_plan": [{"id": "fig1", "type": "plot", "description": "x", "data_source": "exp1"}],
    "citation_plan": {"intro": [], "related": [_KB_HINT], "method": []},
}


def test_kb_only_full_pipeline(tmp_path):
    _seed_kb(tmp_path)
    cfg = _cfg()
    cfg.data["pipeline"]["output_dir"] = str(tmp_path / "out")
    cfg.data["review"]["max_rounds"] = 1
    cfg.data["write"]["dblp_verify"] = False
    cfg.data["write"]["kb_path"] = str(tmp_path)

    client = _MockClient()
    pipeline = Pipeline(cfg, verbose=False, client=client)  # type: ignore[arg-type]
    report = pipeline.run(source="A CNN-ILT method", skip_review=True)
    assert "error" not in report, report.get("error")

    bib = (Path(cfg.output_dir) / "paper" / "references.bib").read_text(encoding="utf-8")
    assert "zhang2024deepilt" in bib, "KB-resolved entry missing from references.bib"
    assert "@article{zhang2024deepilt" in bib

    prompts_text = "".join(user for (_, user) in client.calls)
    assert "KB KNOWN WORK AVAILABLE" in prompts_text, "related-work KB cards block missing"
    assert "Deep Learning for Inverse Lithography" in prompts_text
