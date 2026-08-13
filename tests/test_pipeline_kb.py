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
