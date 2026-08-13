"""Shared fixtures for the Literature KB test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# allow `import kb` when running pytest from the repo root without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import KBStore
from kb.ids import hash_bytes


@pytest.fixture
def tmp_kb(tmp_path):
    """A freshly initialized KB rooted in a temp directory."""
    kbs = KBStore(tmp_path)
    kbs.init()
    return kbs


@pytest.fixture
def make_package():
    """Factory returning a minimal *valid* canonical package dict."""
    def _make(**overrides):
        pkg = {
            "package_spec_version": "1.0",
            "processor": {"name": "paper_to_literature_kb", "version": "0.1.0"},
            "source": {"path": "paper.pdf", "hash": None, "type": "pdf"},
            "paper": {
                "L0": {
                    "paper_id": "",
                    "title": "Deep Learning for Inverse Lithography",
                    "one_line_description": "A CNN-ILT method for mask optimization.",
                    "authors_summary": "Zhang et al.",
                    "year": 2024,
                    "venue": "Optics and Lasers in Engineering",
                    "article_type": "journal",
                    "doi": "10.1016/j.optlaseng.2024.108000",
                    "url": None,
                    "keywords": ["ILT", "deep learning"],
                    "domain_tags": ["ILT"],
                    "method_tags": ["CNN"],
                    "bibliographic_record": {
                        "authors": [{"family": "Zhang", "given": "Wei"}],
                        "title": "Deep Learning for Inverse Lithography",
                        "container_title": "Optics and Lasers in Engineering",
                        "year": 2024,
                        "volume": "178",
                        "pages": "108000",
                        "doi": "10.1016/j.optlaseng.2024.108000",
                    },
                    "citation_key": "",
                    "citation_cache": {
                        "bibtex": "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}",
                    },
                },
                "L1": {
                    "abstract": "We propose a CNN-based ILT method.",
                    "research_problem": "ILT is expensive.",
                    "research_gap": "Prior methods are iterative and slow.",
                    "main_idea": "Learn the inverse mapping directly.",
                    "method_summary": "A CNN maps target to mask.",
                    "main_contributions": ["Learned ILT", "Fast inference"],
                    "innovation": "Direct learning",
                    "key_findings_summary": "Reduces TAT.",
                    "limitations": ["Single pattern type"],
                    "datasets_summary": "MetalSet",
                    "methods_summary": "CNN",
                    "recommended_use": {
                        "background": "moderate",
                        "motivation": "strong",
                        "related_work": "moderate",
                        "method": "strong",
                        "discussion": "weak",
                        "citation_evidence": "moderate",
                    },
                },
                "L2": {
                    "method_card": {
                        "method_name": "CNN-ILT",
                        "method_family": "Deep-learning ILT",
                        "task": "mask optimization",
                        "input": "target pattern",
                        "output": "mask",
                        "architecture": "U-Net",
                        "algorithm": None,
                        "optimization": "L2 loss",
                        "loss_function": "mse",
                        "training_strategy": "supervised",
                        "inference_strategy": "direct",
                        "iterative_or_direct": "direct",
                        "system_context": {"wavelength": 193, "NA": 1.35},
                    },
                    "result_card": {},
                    "metrics": [
                        {
                            "name": "EPE",
                            "value": 2.1,
                            "unit": "nm",
                            "status": "reported",
                            "condition": {"dataset": "MetalSet", "pitch": 45},
                            "baseline": None,
                            "source_evidence_id": "ev001",
                            "source_page": "5",
                            "source_section": "IV",
                        }
                    ],
                    "comparisons": [],
                },
                "L3": {
                    "claims": [
                        {
                            "claim_id": "",
                            "claim": "The method reduces turnaround time.",
                            "claim_type": "comparative",
                            "strength": "B",
                            "supporting_evidence_ids": ["ev001"],
                        }
                    ],
                    "evidence": [
                        {
                            "evidence_id": "",
                            "section": "IV",
                            "page": "5",
                            "source_text": "The proposed method achieves EPE of 2.1 nm.",
                            "claim": "Reduces turnaround time.",
                            "evidence_type": "experimental_result",
                            "metric_refs": ["EPE"],
                            "formula_refs": [],
                        }
                    ],
                },
                "L4": {"fulltext_pointer": "paper.pdf"},
            },
            "formulas": [
                {
                    "formula_id": "",
                    "section": "III",
                    "page": "3",
                    "formula_latex": r"L = \|Z - T\|_2^2",
                    "formula_role": "loss",
                    "semantic_description": "L2 loss between printed and target.",
                    "variables": [{"symbol": "Z", "meaning": "printed pattern"},
                                  {"symbol": "T", "meaning": "target pattern"}],
                    "application": "ILT objective",
                    "reusability": {"directly_reusable": True, "requires_context": False},
                }
            ],
            "citation_records": [],
            "citation_graph": [],
            "validation_report": {},
        }
        pkg.update(overrides)
        return pkg
    return _make


@pytest.fixture
def source_file(tmp_path):
    """A tiny fake source document with stable bytes."""
    p = tmp_path / "paper.pdf"
    p.write_bytes(b"fake-pdf-content-for-source-hash")
    return p


@pytest.fixture
def source_hash(source_file):
    return hash_bytes(source_file.read_bytes())
