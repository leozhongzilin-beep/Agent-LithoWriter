"""Tests for KBStore primitives: counters, reads, atomic write, archive."""

from __future__ import annotations

import sqlite3

import pytest

# ---------------------------------------------------------------------------
# paper_id counter
# ---------------------------------------------------------------------------

def test_next_paper_id_sequences_by_domain_and_year(tmp_kb):
    assert tmp_kb.next_paper_id("ILT", 2024) == "ILT_2024_001"
    assert tmp_kb.next_paper_id("ILT", 2024) == "ILT_2024_002"
    assert tmp_kb.next_paper_id("ILT", 2025) == "ILT_2025_001"
    assert tmp_kb.next_paper_id("SMO", 2024) == "SMO_2024_001"


def test_next_paper_id_no_reuse_after_delete(tmp_kb):
    tmp_kb.next_paper_id("ILT", 2024)  # 001
    tmp_kb.next_paper_id("ILT", 2024)  # 002
    # simulate a delete: counter still advances, never reuses 001
    assert tmp_kb.next_paper_id("ILT", 2024) == "ILT_2024_003"


# ---------------------------------------------------------------------------
# point reads
# ---------------------------------------------------------------------------

def _insert_minimal_paper(tmp_kb, paper_id="ILT_2024_001"):
    tmp_kb.conn.execute(
        "INSERT INTO papers (paper_id, title, year, citation_key, doi, source_hash, created_at, updated_at) "
        "VALUES (?, ?, 2024, ?, ?, ?, 'x', 'x')",
        (paper_id, "Some Title", f"Key{paper_id}2024", "10.1000/x", "sha256:abc"),
    )
    tmp_kb.conn.commit()


def test_find_by_doi_and_hash(tmp_kb):
    _insert_minimal_paper(tmp_kb)
    assert tmp_kb.find_by_doi("10.1000/x") == "ILT_2024_001"
    assert tmp_kb.find_by_doi("10.9999/nope") is None
    assert tmp_kb.find_by_source_hash("sha256:abc") == "ILT_2024_001"
    assert tmp_kb.find_by_citation_key("KeyILT_2024_0012024") == "ILT_2024_001"


def test_find_by_title_normalized(tmp_kb):
    _insert_minimal_paper(tmp_kb)
    assert tmp_kb.find_by_title("Some Title") == ["ILT_2024_001"]
    assert tmp_kb.find_by_title("some   title") == ["ILT_2024_001"]
    assert tmp_kb.find_by_title("Unrelated") == []


def test_paper_exists_and_get(tmp_kb):
    _insert_minimal_paper(tmp_kb)
    assert tmp_kb.paper_exists("ILT_2024_001")
    assert not tmp_kb.paper_exists("ILT_2024_999")
    p = tmp_kb.get_paper("ILT_2024_001")
    assert p["title"] == "Some Title"
    assert tmp_kb.get_paper("ILT_2024_999") is None


# ---------------------------------------------------------------------------
# atomic write + rollback
# ---------------------------------------------------------------------------

def test_write_package_replaces_atomically(tmp_kb):
    pid = "ILT_2024_001"
    first = {"papers": [{"paper_id": pid, "title": "v1", "citation_key": "K1",
                          "created_at": "x", "updated_at": "x"}]}
    tmp_kb.write_package(pid, first,
                         job={"paper_id": pid, "action": "import", "decision": "INSERTED",
                              "created_at": "x"})
    assert tmp_kb.get_paper(pid)["title"] == "v1"

    second = {"papers": [{"paper_id": pid, "title": "v2", "citation_key": "K2",
                           "created_at": "x", "updated_at": "x"}],
               "paper_cards": [{"paper_id": pid, "abstract": "card"}]}
    counts = tmp_kb.write_package(pid, second,
                                  job={"paper_id": pid, "action": "import", "decision": "UPDATED",
                                       "created_at": "x"})
    assert tmp_kb.get_paper(pid)["title"] == "v2"
    assert counts.get("paper_cards") == 1
    # old citation_key row gone
    assert tmp_kb.find_by_citation_key("K1") is None
    assert tmp_kb.find_by_citation_key("K2") == pid


def test_write_package_rolls_back_on_error(tmp_kb):
    """A mid-write IntegrityError must leave zero partial rows."""
    pid = "ILT_2024_001"
    # duplicate (paper_id, style_id) inside citation_records violates UNIQUE
    bad = {
        "papers": [{"paper_id": pid, "title": "t", "citation_key": "K",
                     "created_at": "x", "updated_at": "x"}],
        "paper_cards": [{"paper_id": pid, "abstract": "card"}],
        "citation_records": [
            {"paper_id": pid, "citation_key": "K", "style_id": "ieee"},
            {"paper_id": pid, "citation_key": "K", "style_id": "ieee"},
        ],
    }
    with pytest.raises(sqlite3.IntegrityError):
        tmp_kb.write_package(pid, bad,
                             job={"paper_id": pid, "action": "import",
                                  "decision": "INSERTED", "created_at": "x"})
    assert not tmp_kb.paper_exists(pid)
    assert tmp_kb.conn.execute("SELECT COUNT(*) FROM paper_cards").fetchone()[0] == 0
    assert tmp_kb.conn.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# archive files
# ---------------------------------------------------------------------------

def test_archive_roundtrip(tmp_kb, make_package, source_file):
    pid = "ILT_2024_001"
    tmp_kb.write_package_archive(pid, make_package())
    manifest = tmp_kb.write_manifest(pid, {"paper_id": pid, "decision": "INSERTED"})
    assert tmp_kb.paper_dir(pid).exists()
    assert (tmp_kb.paper_dir(pid) / "package.json").exists()
    assert manifest.exists()

    copied = tmp_kb.archive_source(pid, source_file)
    assert copied.name == source_file.name
    assert copied.read_bytes() == source_file.read_bytes()
    assert tmp_kb.source_archived(pid) == copied


def test_dangling_sources(tmp_kb):
    pid = "ILT_2024_001"
    tmp_kb.conn.execute(
        "INSERT INTO papers (paper_id, title, citation_key, source_reachable, created_at, updated_at) "
        "VALUES (?, 't', 'K', 0, 'x', 'x')",
        (pid,),
    )
    tmp_kb.conn.commit()
    assert tmp_kb.dangling_sources() == [pid]
