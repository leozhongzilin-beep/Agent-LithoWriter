"""Tests for package load / normalize / validate (kb/package.py)."""

from __future__ import annotations

import json

import pytest
from kb.package import (
    PackageError,
    load_package,
    normalize_package,
    suggested_paper_id,
    title,
    validate_package,
)


def test_load_json_package(tmp_path, make_package):
    p = tmp_path / "package.json"
    p.write_text(json.dumps(make_package()), encoding="utf-8")
    data = load_package(p)
    assert title(data) == "Deep Learning for Inverse Lithography"


def test_load_yaml_package(tmp_path, make_package):
    yaml = pytest.importorskip("yaml")
    p = tmp_path / "package.yaml"
    p.write_text(yaml.safe_dump(make_package()), encoding="utf-8")
    data = load_package(p)
    assert title(data) == "Deep Learning for Inverse Lithography"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(PackageError):
        load_package(tmp_path / "nope.json")


def test_normalize_fills_defaults(make_package):
    data = normalize_package(make_package())
    assert data["processor"]["name"] == "paper_to_literature_kb"
    assert data["source"]["type"] == "pdf"
    assert data["paper"]["L2"]["metrics"] == [data["paper"]["L2"]["metrics"][0]]
    assert data["paper"]["L3"]["evidence"][0]["source_text"] == (
        "The proposed method achieves EPE of 2.1 nm."
    )


def test_normalize_never_mutates_input(make_package):
    raw = make_package()
    before = json.dumps(raw, sort_keys=True)
    normalize_package(raw)
    assert json.dumps(raw, sort_keys=True) == before


def test_validate_valid_package_no_errors(make_package):
    errors, warnings = validate_package(normalize_package(make_package()))
    assert errors == []
    assert warnings == []


def test_validate_blocks_reported_metric_without_evidence(make_package):
    pkg = make_package()
    pkg["paper"]["L2"]["metrics"][0]["source_evidence_id"] = None
    errors, _ = validate_package(normalize_package(pkg))
    assert any("source_evidence_id" in e for e in errors)


def test_validate_warns_on_missing_unit_for_reported_number(make_package):
    pkg = make_package()
    pkg["paper"]["L2"]["metrics"][0]["unit"] = None
    _, warnings = validate_package(normalize_package(pkg))
    assert any("unit" in w for w in warnings)


def test_validate_blocks_bad_metric_status(make_package):
    pkg = make_package()
    pkg["paper"]["L2"]["metrics"][0]["status"] = "guessed"
    errors, _ = validate_package(normalize_package(pkg))
    assert any("status" in e for e in errors)


def test_validate_blocks_duplicate_citation_keys(make_package):
    pkg = make_package()
    pkg["citation_records"] = [
        {"style_id": "ieee", "citation_key": "dup"},
        {"style_id": "nature", "citation_key": "dup"},
    ]
    errors, _ = validate_package(normalize_package(pkg))
    assert any("duplicate citation_key" in e for e in errors)


def test_suggested_paper_id(make_package):
    pkg = make_package()
    assert suggested_paper_id(pkg) == ""
    pkg["paper"]["L0"]["paper_id"] = "ILT_2024_042"
    assert suggested_paper_id(pkg) == "ILT_2024_042"
