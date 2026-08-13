"""Validation (Skill step 12) + human-review triggers (Skill §18).

Reuses the KB's canonical quality gates (kb.package.validate_package) so the
skill never re-implements validation; additionally flags the spec's
human-review conditions (unclear/figure-estimated metrics, missing conditions,
unknown formula variables, evidence without page/section).
"""

from __future__ import annotations

from typing import Any

from ._kb import kb_package


def validate(package: dict[str, Any]) -> dict[str, Any]:
    """Run the KB gates + human-review scan; returns the validation_report."""
    errors, warnings = kb_package().validate_package(package)
    gates = {f"QG-{i}": not any(e.startswith(f"QG-{i}") for e in errors)
             for i in range(1, 6)}
    return {
        "gates": gates,
        "pass": not errors,
        "errors": errors,
        "warnings": warnings,
        "human_review": human_review_triggers(package),
    }


def human_review_triggers(package: dict[str, Any]) -> list[str]:
    """Spec §18 conditions that must enter the human-review queue."""
    triggers: list[str] = []
    metrics = (package.get("paper", {}).get("L2", {}) or {}).get("metrics", [])
    for m in metrics:
        name = m.get("name", "?")
        if m.get("status") == "reported" and not m.get("condition"):
            triggers.append(f"metric {name!r}: reported but no experimental condition")
        if m.get("status") == "unclear":
            triggers.append(f"metric {name!r}: status unclear (possibly figure-estimated)")

    for f in package.get("formulas", []):
        if any(v.get("meaning") == "unclear"
               for v in (f.get("variables") or []) if isinstance(v, dict)):
            triggers.append(f"formula {str(f.get('formula_latex', ''))[:40]!r}: "
                            "variable meaning unknown")

    evidence = (package.get("paper", {}).get("L3", {}) or {}).get("evidence", [])
    for e in evidence:
        if not e.get("section") and not e.get("page"):
            triggers.append("evidence block without section/page")

    return triggers
