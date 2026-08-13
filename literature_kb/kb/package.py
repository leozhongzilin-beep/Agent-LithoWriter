"""Canonical package model for the Literature Knowledge Base.

The Skill (`paper_to_literature_kb`) emits one package per paper. On disk the
canonical form is JSON; YAML input is accepted at load and normalized to the
same dict shape. `kb add` requires zero validation *errors* (warnings are
allowed and are carried into the validation report / processing_jobs).

Canonical package shape (header + Skill output contract):

    {
      "package_spec_version": "1.0",
      "processor": {"name": "...", "version": "..."},
      "source":    {"path": "...", "hash": "sha256:...", "type": "pdf"},
      "paper": {
          "L0": {...}, "L1": {...},
          "L2": {"method_card": {...}, "result_card": {...},
                 "metrics": [...], "comparisons": [...]},
          "L3": {"claims": [...], "evidence": [...]},
          "L4": {"fulltext_pointer": "..."},
      },
      "formulas": [],
      "citation_records": [],
      "citation_graph": [],
      "validation_report": {}
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PACKAGE_SPEC_VERSION = "1.0"

_METRIC_STATUSES = {"reported", "not_reported", "not_applicable", "unclear"}
_COMPARISON_VALIDITY = {"comparable", "partially_comparable", "not_comparable"}
_CLAIM_TYPES = {"definition", "methodological", "quantitative", "comparative",
                "causal", "limitation", "conclusion"}
_EVIDENCE_TYPES = {"definition", "methodological_statement", "observation",
                   "experimental_result", "comparison", "limitation",
                   "causal_claim", "quantitative_result"}
_STRENGTHS = {"A", "B", "C", "D"}
_DOI_RE = re.compile(r"^10\.\d{4,9}/")


class PackageError(Exception):
    """Raised when a package cannot be loaded or normalized."""


def load_package(path) -> dict[str, Any]:
    """Load a package file. JSON preferred; YAML accepted when PyYAML exists."""
    p = Path(path)
    if not p.exists():
        raise PackageError(f"package file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"cannot read {p}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _load_yaml(text)
    if not isinstance(data, dict):
        raise PackageError(f"{p}: package must be a JSON/YAML object")
    return data


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise PackageError(
            "package is not valid JSON and PyYAML is not installed; "
            "`pip install pyyaml` to accept YAML packages"
        ) from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PackageError("package must be a YAML object")
    return data


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _bool_str(value: Any) -> str:
    """Normalize a bool-ish value to 'true'/'false' for JSON reusability fields."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).lower()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_package(data: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults and coerce types so downstream code can trust the shape.

    Returns a NEW dict — input is never mutated (deep-copied first).
    """
    import copy
    out: dict[str, Any] = copy.deepcopy(data)
    out.setdefault("package_spec_version", PACKAGE_SPEC_VERSION)
    proc = out.get("processor")
    out["processor"] = {
        "name": (proc or {}).get("name") or "unknown",
        "version": (proc or {}).get("version") or "0.0.0",
    }
    src = out.get("source") or {}
    out["source"] = {
        "path": src.get("path"),
        "hash": src.get("hash"),
        "type": src.get("type") or "unknown",
    }

    paper = dict(out.get("paper") or {})
    for key in ("L0", "L1", "L2", "L3", "L4"):
        paper[key] = dict(paper.get(key) or {})
    L2 = dict(paper["L2"])
    L2.setdefault("method_card", {})
    L2.setdefault("result_card", {})
    L2["metrics"] = _as_list(L2.get("metrics"))
    L2["comparisons"] = _as_list(L2.get("comparisons"))
    L3 = dict(paper["L3"])
    L3["claims"] = _as_list(L3.get("claims"))
    L3["evidence"] = _as_list(L3.get("evidence"))
    paper["L2"] = L2
    paper["L3"] = L3
    out["paper"] = paper

    out["formulas"] = _as_list(out.get("formulas"))
    out["citation_records"] = _as_list(out.get("citation_records"))
    out["citation_graph"] = _as_list(out.get("citation_graph"))
    out.setdefault("validation_report", {})

    # normalize bool-ish reusability to strings for JSON
    for f in out["formulas"]:
        if isinstance(f, dict) and isinstance(f.get("reusability"), dict):
            r = dict(f["reusability"])
            for k in ("directly_reusable", "requires_context"):
                if k in r:
                    r[k] = _bool_str(r[k])
            f["reusability"] = r
    return out


# ---------------------------------------------------------------------------
# Validation (Quality Gates, light form)
# ---------------------------------------------------------------------------

def validate_package(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block import; warnings do not."""
    errors: list[str] = []
    warnings: list[str] = []
    paper = data.get("paper") or {}
    L0 = paper.get("L0") or {}

    # ---- QG-1 Metadata ----
    if not (L0.get("title") or "").strip():
        errors.append("QG-1: L0.title is empty")
    year = L0.get("year")
    if year is not None and not str(year).isdigit():
        errors.append(f"QG-1: L0.year is not an integer year: {year!r}")
    doi = L0.get("doi")
    if doi and not _DOI_RE.match(doi):
        warnings.append(f"QG-1: doi {doi!r} does not look like a DOI (syntax only)")

    # ---- QG-4 Citation ----
    keys = [m.get("citation_key") for m in _as_list(data.get("citation_records"))
            if isinstance(m, dict) and m.get("citation_key")]
    if len(set(keys)) != len(keys):
        errors.append("QG-4: duplicate citation_key values within the package")
    if not (L0.get("bibliographic_record") or L0.get("authors_summary")):
        warnings.append("QG-4: no bibliographic_record / authors_summary; rendering may be impossible")

    # ---- QG-2 Numeric facts ----
    for m in _as_list((paper.get("L2") or {}).get("metrics")):
        if not isinstance(m, dict):
            errors.append("QG-2: metric entry is not an object")
            continue
        name = m.get("name")
        status = m.get("status")
        if not name:
            errors.append("QG-2: metric missing 'name'")
        if status not in _METRIC_STATUSES:
            errors.append(f"QG-2: metric {name!r} status {status!r} not in {sorted(_METRIC_STATUSES)}")
        if status == "reported":
            if m.get("value") is None and not (m.get("value_text") or "").strip():
                warnings.append(f"QG-2: metric {name!r} reported but has no value/value_text")
            if not m.get("source_evidence_id"):
                errors.append(f"QG-2: reported metric {name!r} lacks source_evidence_id")
            if not m.get("unit") and _metric_should_have_unit(m):
                warnings.append(f"QG-2: metric {name!r} has no unit")

    # ---- QG-3 Formula ----
    for f in _as_list(data.get("formulas")):
        if not isinstance(f, dict):
            errors.append("QG-3: formula entry is not an object")
            continue
        if not (f.get("formula_latex") or "").strip():
            errors.append("QG-3: formula missing formula_latex")
        if not f.get("formula_role"):
            warnings.append("QG-3: formula has no formula_role")
        for v in _as_list(f.get("variables")):
            if isinstance(v, dict) and not v.get("symbol"):
                errors.append("QG-3: formula variable missing 'symbol'")

    # ---- QG-5 Evidence ----
    for c in _as_list((paper.get("L3") or {}).get("claims")):
        if not isinstance(c, dict) or not (c.get("claim") or "").strip():
            errors.append("QG-5: claim missing 'claim' text")
    for ev in _as_list((paper.get("L3") or {}).get("evidence")):
        if not isinstance(ev, dict):
            errors.append("QG-5: evidence entry is not an object")
            continue
        if not (ev.get("source_text") or "").strip():
            errors.append("QG-5: evidence missing source_text (must be verbatim)")
        if not ev.get("section") and not ev.get("page"):
            warnings.append("QG-5: evidence has neither section nor page")

    return errors, warnings


def _metric_should_have_unit(m: dict[str, Any]) -> bool:
    """Heuristic: a raw number usually needs a unit; a ratio/bool usually not."""
    if m.get("value") is None:
        return False
    name = str(m.get("name") or "").lower()
    return not any(t in name for t in ("iou", "accuracy", "f1", "dice", "ratio", "loss"))


def suggested_paper_id(data: dict[str, Any]) -> str:
    """The paper_id carried by the package (may be empty — assigned at import)."""
    L0 = (data.get("paper") or {}).get("L0") or {}
    return str(L0.get("paper_id") or "").strip()


def title(data: dict[str, Any]) -> str:
    L0 = (data.get("paper") or {}).get("L0") or {}
    return str(L0.get("title") or "").strip()
