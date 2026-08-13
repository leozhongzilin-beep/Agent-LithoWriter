"""Tests for citation resolution (kb/citation.py)."""

from __future__ import annotations

from kb.citation import resolve_citation
from kb.importtool import import_package


def test_resolve_from_cache(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    tmp_kb.conn.execute(
        "INSERT INTO citation_records (paper_id, citation_key, style_id, "
        "in_text_citation, bibliography_entry) VALUES (?,?,?,?,?)",
        (res.paper_id, "Zhang2024DeepLearning", "ieee",
         "[1]", "W. Zhang, Deep Learning for ILT, 2024."),
    )
    tmp_kb.conn.commit()
    out = resolve_citation(tmp_kb, res.paper_id, style_id="ieee")
    assert out["generated"] is False
    assert out["bibliography_entry"] == "W. Zhang, Deep Learning for ILT, 2024."
    assert out["citation_key"] == "Zhang2024DeepLearning"


def test_resolve_fallback_generates_short_citation(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    out = resolve_citation(tmp_kb, res.paper_id, style_id="nature")
    assert out["generated"] is True  # explicitly flagged, never silent
    assert "Zhang" in out["in_text_citation"]
    assert "2024" in out["in_text_citation"]


def test_resolve_unknown_paper_returns_none(tmp_kb):
    assert resolve_citation(tmp_kb, "ILT_9999_999") is None
