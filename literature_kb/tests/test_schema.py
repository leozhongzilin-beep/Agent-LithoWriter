"""Tests for schema creation and idempotency."""

from __future__ import annotations

from kb.schema import SCHEMA_VERSION, TABLES, list_tables


def test_init_creates_all_tables(tmp_kb):
    present = set(list_tables(tmp_kb.conn))
    for t in TABLES:
        assert t in present, f"missing table {t}"


def test_init_is_idempotent(tmp_kb):
    tmp_kb.init()
    first = set(list_tables(tmp_kb.conn))
    tmp_kb.init()
    # re-init adds nothing (no duplicate base or FTS/shadow tables)
    assert set(list_tables(tmp_kb.conn)) == first


def test_schema_version_pinned():
    assert SCHEMA_VERSION == "1.0"


def test_foreign_keys_enforced(tmp_kb):
    import sqlite3
    # referencing a nonexistent paper must fail
    try:
        tmp_kb.conn.execute(
            "INSERT INTO paper_cards (paper_id, abstract) VALUES ('NOPE', 'x')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_metric_status_check_constraint(tmp_kb):
    import sqlite3
    tmp_kb.conn.execute(
        "INSERT INTO papers (paper_id, title, citation_key, created_at, updated_at) "
        "VALUES ('ILT_2024_001','t','Key2024T','x','x')"
    )
    try:
        tmp_kb.conn.execute(
            "INSERT INTO paper_metrics (metric_id, paper_id, name, status) "
            "VALUES ('ILT_2024_001.mt001','ILT_2024_001','EPE','bogus')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
