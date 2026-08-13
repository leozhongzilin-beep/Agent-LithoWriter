"""Tests for formula search (kb/formula.py)."""

from __future__ import annotations

from kb.formula import search_formulas
from kb.importtool import import_package


def test_search_formulas_by_keyword(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    hits = search_formulas(tmp_kb, "loss")
    assert hits and hits[0].paper_id == res.paper_id
    f = hits[0]
    assert f.formula_role == "loss"
    assert "L" in f.formula_latex or "Z" in f.formula_latex
    # variables carry meanings, not just symbols
    by_symbol = {v["symbol"]: v["meaning"] for v in f.variables}
    assert by_symbol["Z"] == "printed pattern"
    assert by_symbol["T"] == "target pattern"


def test_search_formulas_role_filter(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    assert search_formulas(tmp_kb, "loss", role="objective") == []
    assert search_formulas(tmp_kb, "loss", role="loss")  # exact role matches


def test_search_formulas_no_match_returns_empty(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    assert search_formulas(tmp_kb, "quantum tunneling") == []
