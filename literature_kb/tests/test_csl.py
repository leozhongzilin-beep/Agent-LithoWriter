"""Tests for CSL citation rendering (kb/csl.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("citeproc")  # CSL rendering is a declared, runtime dep

from kb import csl
from kb.citation import resolve_citation
from kb.importtool import import_package


def test_render_bibliography(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    entries = csl.render_bibliography(tmp_kb, [res.paper_id], "author-date")
    assert entries and "Zhang" in entries[0]
    assert "2024" in entries[0]
    assert "Inverse Lithography" in entries[0]


def test_render_in_text(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    assert csl.render_in_text(tmp_kb, res.paper_id, "author-date") == "(Zhang, 2024)"


def test_unknown_style_raises(tmp_kb):
    with pytest.raises(csl.StyleUnavailable):
        csl.load_style(tmp_kb, "nope")


def test_bundled_styles_and_seed(tmp_kb):
    assert "author-date" in csl.bundled_style_ids()
    counts = csl.seed_styles(tmp_kb)
    assert counts["citation_styles"] == len(csl.bundled_style_ids())
    assert csl.resolve_style_path(tmp_kb, "author-date") is not None
    # idempotent
    assert csl.seed_styles(tmp_kb)["citation_styles"] == counts["citation_styles"]


def test_resolve_citation_prefers_csl(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    out = resolve_citation(tmp_kb, res.paper_id, style_id="author-date")
    assert out["generated"] is False
    assert out["in_text_citation"] == "(Zhang, 2024)"
    assert out["bibliography_entry"] and "Zhang" in out["bibliography_entry"]


def test_resolve_citation_falls_back_on_missing_style(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    out = resolve_citation(tmp_kb, res.paper_id, style_id="not-a-style")
    assert out["generated"] is True  # minimal fallback, explicitly flagged
