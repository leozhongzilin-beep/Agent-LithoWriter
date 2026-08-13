"""Tests for the FTS5 index layer (kb/fts.py)."""

from __future__ import annotations

from kb import fts


def _paper_rows(paper_id="ILT_2024_001", title="Deep Learning for Inverse Lithography"):
    return {
        "papers": [{
            "paper_id": paper_id, "title": title,
            "keywords": '["ILT","deep learning"]',
            "domain_tags": '["ILT"]', "method_tags": '["CNN"]',
            "one_line_description": "A CNN-ILT method for mask optimization.",
        }],
        "paper_evidence": [{
            "paper_id": paper_id, "evidence_id": f"{paper_id}.ev001",
            "source_text": "The proposed method achieves EPE of 2.1 nm.",
            "claim": "Reduces turnaround time.",
            "section": "IV",
        }],
        "formulas": [{
            "paper_id": paper_id, "formula_id": f"{paper_id}.fm001",
            "formula_latex": r"L = \|Z - T\|_2^2",
            "semantic_description": "L2 loss between printed and target.",
            "variables": '["Z","T"]',
        }],
    }


def test_create_fts_makes_virtual_tables(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    placeholders = ", ".join("?" * len(fts.FTS_TABLES))
    tables = [r[0] for r in tmp_kb.conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        list(fts.FTS_TABLES)).fetchall()]
    assert set(tables) == set(fts.FTS_TABLES)


def test_sync_rows_indexes_paper_and_query_finds_it(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", _paper_rows())
    hits = fts.query(tmp_kb.conn, "fts_papers", "lithography")
    assert [h["paper_id"] for h in hits] == ["ILT_2024_001"]


def test_sync_rows_is_replace_not_append(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    rows = _paper_rows()
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", rows)
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", rows)  # re-sync
    hits = fts.query(tmp_kb.conn, "fts_papers", "lithography")
    assert len(hits) == 1


def test_evidence_and_formula_indexed(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", _paper_rows())
    ev = fts.query(tmp_kb.conn, "fts_evidence", "EPE")
    assert [h["evidence_id"] for h in ev] == ["ILT_2024_001.ev001"]
    fm = fts.query(tmp_kb.conn, "fts_formulas", "loss")
    assert [h["formula_id"] for h in fm] == ["ILT_2024_001.fm001"]


def test_query_sorted_by_relevance(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", _paper_rows(title="Lithography ILT"))
    fts.sync_rows(tmp_kb.conn, "ILT_2024_002", _paper_rows(
        paper_id="ILT_2024_002",
        title="A Completely Unrelated Paper About Batteries"))
    hits = fts.query(tmp_kb.conn, "fts_papers", "lithography")
    assert next(h["paper_id"] for h in hits) == "ILT_2024_001"
    for i in range(len(hits) - 1):  # best-first ordering
        assert hits[i]["score"] >= hits[i + 1]["score"]


def test_make_match_escapes_user_input():
    # FTS5 operator characters are neutralized by quoting every token
    assert fts.make_match('Deep "Learning" ILT') == '"Deep" AND "Learning" AND "ILT"'
    assert fts.make_match("") == ""


def test_query_with_empty_match_returns_nothing(tmp_kb):
    fts.create_fts(tmp_kb.conn)
    fts.sync_rows(tmp_kb.conn, "ILT_2024_001", _paper_rows())
    assert fts.query(tmp_kb.conn, "fts_papers", "") == []
    assert fts.query(tmp_kb.conn, "fts_papers", "!!!") == []
