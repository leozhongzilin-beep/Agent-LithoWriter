"""End-to-end tests for kb.add semantics (kb/importtool.py).

Covers the frozen import ladder:
    paper_id -> DOI -> source_hash -> title,
plus change detection (SOURCE_CHANGED / EXTRACTION_UPDATED), atomic
replacement, citation-key dedup, and archive provenance.
"""

from __future__ import annotations

import json

import pytest
from kb import fts
from kb.importtool import ImportBlocked, import_package
from kb.package import PackageError


def _import(tmp_kb, pkg, **kw):
    return import_package(tmp_kb, pkg, **kw)


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

def test_insert_new_paper(tmp_kb, make_package):
    res = _import(tmp_kb, make_package())
    assert res.decision == "INSERTED"
    assert res.paper_id == "ILT_2024_001"
    assert res.source_hash is None  # no source file given, header hash None
    # only the (expected) SOURCE_UNREACHABLE note — no change/identity warnings
    assert not any(w.startswith(("SOURCE_CHANGED", "DUPLICATE",
                                 "POSSIBLE_DUPLICATE", "CITATION_KEY_DEDUP"))
                   for w in res.warnings)

    paper = tmp_kb.get_paper(res.paper_id)
    assert paper["title"] == "Deep Learning for Inverse Lithography"
    assert paper["citation_key"] == "Zhang2024DeepLearning"
    assert paper["bibtex_key"] == "zhang2024deepilt"
    assert paper["doi"] == "10.1016/j.optlaseng.2024.108000"

    # rows landed
    counts = tmp_kb.table_counts()
    assert counts["paper_metrics"] == 1
    assert counts["paper_evidence"] == 1
    assert counts["paper_claims"] == 1
    assert counts["formulas"] == 1
    assert counts["formula_variables"] == 2
    assert counts["processing_jobs"] == 1
    assert counts["validation_reports"] == 1

    # archive written
    pdir = tmp_kb.paper_dir(res.paper_id)
    assert (pdir / "package.json").exists()
    assert (pdir / "manifest.json").exists()


def test_insert_with_source_archives_and_hashes(tmp_kb, make_package, source_file, source_hash):
    res = _import(tmp_kb, make_package(), source_path=str(source_file))
    assert res.source_hash == source_hash
    assert res.archived_source is not None
    assert res.archived_source.exists()
    assert tmp_kb.get_paper(res.paper_id)["source_reachable"] == 1
    assert tmp_kb.get_paper(res.paper_id)["source_hash"] == source_hash


def test_insert_without_source_marks_unreachable(tmp_kb, make_package):
    res = _import(tmp_kb, make_package())  # header hash None, no --source
    assert res.source_hash is None
    paper = tmp_kb.get_paper(res.paper_id)
    assert paper["source_reachable"] == 0
    assert any("SOURCE_UNREACHABLE" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# UPDATED — same paper_id
# ---------------------------------------------------------------------------

def test_upsert_same_paper_id(tmp_kb, make_package):
    first = _import(tmp_kb, make_package())
    second_pkg = make_package(paper={"L0": {**make_package()["paper"]["L0"],
                                            "paper_id": first.paper_id},
                                     "L1": make_package()["paper"]["L1"],
                                     "L2": make_package()["paper"]["L2"],
                                     "L3": make_package()["paper"]["L3"],
                                     "L4": make_package()["paper"]["L4"]})
    res = _import(tmp_kb, second_pkg)
    assert res.decision == "UPDATED"
    assert res.paper_id == first.paper_id
    # rows not duplicated
    assert tmp_kb.table_counts()["papers"] == 1
    assert tmp_kb.table_counts()["paper_metrics"] == 1
    # audit log has both events
    assert tmp_kb.table_counts()["processing_jobs"] == 2


# ---------------------------------------------------------------------------
# DOI resolution
# ---------------------------------------------------------------------------

def test_doi_resolves_to_existing(tmp_kb, make_package):
    first = _import(tmp_kb, make_package())  # ILT_2024_001 with doi 10.1016/...

    # new package, empty paper_id, same DOI
    pkg = make_package()
    pkg["paper"]["L0"]["paper_id"] = ""
    pkg["paper"]["L0"]["title"] = "A Renamed Title"
    pkg["citation_records"] = []  # keep unique
    res = _import(tmp_kb, pkg)
    assert res.decision == "UPDATED"
    assert res.paper_id == first.paper_id  # resolved via DOI, not a new paper
    assert tmp_kb.table_counts()["papers"] == 1
    # the new title replaced the old one
    assert tmp_kb.get_paper(first.paper_id)["title"] == "A Renamed Title"


def test_doi_resolve_with_different_suggested_id_warns(tmp_kb, make_package):
    first = _import(tmp_kb, make_package())
    pkg = make_package()
    pkg["paper"]["L0"]["paper_id"] = "ILT_2024_099"
    res = _import(tmp_kb, pkg)
    assert res.decision == "UPDATED"
    assert res.paper_id == first.paper_id
    assert any("REASSIGNED_ID" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# DUPLICATE_SOURCE
# ---------------------------------------------------------------------------

def test_duplicate_source_blocks_without_force(tmp_kb, make_package, source_file):
    first = _import(tmp_kb, make_package(), source_path=str(source_file))

    pkg = make_package()
    pkg["paper"]["L0"]["doi"] = "10.9999/different"   # force a different identity
    pkg["paper"]["L0"]["title"] = "Completely Different Paper"
    with pytest.raises(ImportBlocked, match="DUPLICATE_SOURCE"):
        _import(tmp_kb, pkg, source_path=str(source_file))
    # nothing was imported
    assert tmp_kb.table_counts()["papers"] == 1
    assert tmp_kb.get_paper(first.paper_id)["title"] == "Deep Learning for Inverse Lithography"


def test_duplicate_source_force_proceeds(tmp_kb, make_package, source_file):
    _import(tmp_kb, make_package(), source_path=str(source_file))
    pkg = make_package()
    pkg["paper"]["L0"]["doi"] = "10.9999/different"
    pkg["paper"]["L0"]["title"] = "Completely Different Paper"
    res = _import(tmp_kb, pkg, source_path=str(source_file), force=True)
    assert res.decision == "INSERTED"
    assert res.paper_id == "ILT_2024_002"
    assert any("DUPLICATE_SOURCE" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# POSSIBLE_DUPLICATE (title match only)
# ---------------------------------------------------------------------------

def test_title_match_is_note_only(tmp_kb, make_package):
    _import(tmp_kb, make_package())
    pkg = make_package()
    pkg["paper"]["L0"]["doi"] = "10.9999/another"
    pkg["paper"]["L0"]["year"] = 2024
    res = _import(tmp_kb, pkg)
    assert res.decision == "INSERTED"
    assert res.paper_id == "ILT_2024_002"
    assert any("POSSIBLE_DUPLICATE" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# change detection on re-import
# ---------------------------------------------------------------------------

def test_source_changed_warns_but_allows(tmp_kb, make_package, source_file):
    first = _import(tmp_kb, make_package(), source_path=str(source_file))

    # mutate the source -> new hash
    mutated = tmp_kb.root / "mutated.pdf"
    mutated.write_bytes(source_file.read_bytes() + b"CHANGED")

    pkg = make_package()
    pkg["paper"]["L0"]["paper_id"] = first.paper_id
    res = _import(tmp_kb, pkg, source_path=str(mutated))
    assert res.decision == "UPDATED"
    assert any("SOURCE_CHANGED" in w for w in res.warnings)
    # data was replaced (allowed)
    assert tmp_kb.table_counts()["papers"] == 1


def test_extraction_updated_warns_when_processor_changes(tmp_kb, make_package, source_file):
    _import(tmp_kb, make_package(), source_path=str(source_file))
    first = tmp_kb.list_papers()[0]

    pkg = make_package()  # same source, same content
    pkg["paper"]["L0"]["paper_id"] = first["paper_id"]
    pkg["processor"]["version"] = "0.2.0"  # re-extracted with a newer processor
    res = _import(tmp_kb, pkg, source_path=str(source_file))
    assert res.decision == "UPDATED"
    assert any("EXTRACTION_UPDATED" in w for w in res.warnings)
    assert not any("SOURCE_CHANGED" in w for w in res.warnings)


def test_no_change_no_warning(tmp_kb, make_package, source_file):
    _import(tmp_kb, make_package(), source_path=str(source_file))
    first = tmp_kb.list_papers()[0]
    pkg = make_package()
    pkg["paper"]["L0"]["paper_id"] = first["paper_id"]
    res = _import(tmp_kb, pkg, source_path=str(source_file))
    assert res.decision == "UPDATED"
    assert res.warnings == []


# ---------------------------------------------------------------------------
# citation key dedup + validation gates
# ---------------------------------------------------------------------------

def test_citation_key_dedup_on_conflict(tmp_kb, make_package):
    _import(tmp_kb, make_package())
    pkg = make_package()
    pkg["paper"]["L0"]["doi"] = "10.9999/another"
    pkg["paper"]["L0"]["year"] = 2024
    pkg["paper"]["L0"]["title"] = "Deep Learning for Inverse Lithography"
    res = _import(tmp_kb, pkg)
    # same first author+year+title -> same base key -> deduped
    keys = [p["citation_key"] for p in tmp_kb.list_papers()]
    assert len(keys) == len(set(keys))
    assert any(w.startswith("CITATION_KEY_DEDUP") for w in res.warnings)


def test_invalid_package_blocked(tmp_kb, make_package):
    pkg = make_package()
    pkg["paper"]["L0"]["title"] = ""
    with pytest.raises(PackageError, match="QG-1"):
        _import(tmp_kb, pkg)


def test_auto_assign_requires_year(tmp_kb, make_package):
    pkg = make_package()
    pkg["paper"]["L0"]["year"] = None
    with pytest.raises(PackageError, match="--paper-id"):
        _import(tmp_kb, pkg)


def test_paper_id_override(tmp_kb, make_package):
    res = _import(tmp_kb, make_package(), paper_id_override="SMO_2025_017")
    assert res.paper_id == "SMO_2025_017"
    assert res.decision == "INSERTED"


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def test_processing_jobs_is_audit_log(tmp_kb, make_package):
    _import(tmp_kb, make_package())
    rows = tmp_kb.conn.execute(
        "SELECT action, decision FROM processing_jobs"
    ).fetchall()
    assert [dict(r) for r in rows] == [{"action": "import", "decision": "INSERTED"}]


def test_import_populates_fts(tmp_kb, make_package):
    """kb add must make a paper immediately searchable (FTS synced in-txn)."""
    res = _import(tmp_kb, make_package())
    hits = fts.query(tmp_kb.conn, "fts_papers", "lithography")
    assert [h["paper_id"] for h in hits] == [res.paper_id]
    assert fts.query(tmp_kb.conn, "fts_evidence", "EPE") != []
    assert fts.query(tmp_kb.conn, "fts_formulas", "loss") != []


def test_manifest_records_provenance(tmp_kb, make_package):
    res = _import(tmp_kb, make_package())
    manifest = json.loads((tmp_kb.paper_dir(res.paper_id) / "manifest.json").read_text())
    assert manifest["decision"] == "INSERTED"
    assert manifest["citation_key"] == "Zhang2024DeepLearning"
    assert "row_counts" in manifest and manifest["row_counts"]["paper_metrics"] == 1
