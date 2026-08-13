"""Ontology seeding + concept expansion (PRD KB-Completion group C).

Curated `concepts` + `metrics_ontology` content ships in kb/seeds/*.yaml.
`seed()` is idempotent; `validate_seed()` rejects orphan parent/child refs and
duplicate ids; `alias_map()` builds the alias -> [canonical, children] mapping
that activates the retrieval router's concept-expansion hook.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import KBStore, _j, _jload

_SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
_CONCEPT_FIELDS = (
    "canonical_name", "aliases", "parent_concepts", "child_concepts",
    "related_concepts", "related_methods", "related_papers",
)
_METRIC_FIELDS = (
    "canonical_definition", "aliases", "unit", "category",
    "measurement_scope", "comparability_rules", "common_pitfalls",
)
# fields stored as JSON arrays in the schema
_JSON_FIELDS = ("aliases", "parent_concepts", "child_concepts",
                "related_concepts", "related_methods", "related_papers")


def load_seed(path: Path | None = None) -> dict[str, Any]:
    """Load seed data from YAML. Default: bundled kb/seeds/*.yaml."""
    import yaml

    if path is not None:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    out: dict[str, Any] = {"concepts": [], "metrics": []}
    concepts_file = _SEEDS_DIR / "concepts.yaml"
    metrics_file = _SEEDS_DIR / "metrics.yaml"
    if concepts_file.exists():
        out["concepts"] = (yaml.safe_load(
            concepts_file.read_text(encoding="utf-8")) or {}).get("concepts", [])
    if metrics_file.exists():
        out["metrics"] = (yaml.safe_load(
            metrics_file.read_text(encoding="utf-8")) or {}).get("metrics", [])
    return out


def validate_seed(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block seeding; warnings do not."""
    errors: list[str] = []
    warnings: list[str] = []

    concepts = data.get("concepts", [])
    ids = {c.get("concept_id") for c in concepts if isinstance(c, dict)}
    seen: set[str] = set()
    for c in concepts:
        if not isinstance(c, dict):
            errors.append("concept entry is not an object")
            continue
        cid = c.get("concept_id")
        if not cid:
            errors.append("concept missing concept_id")
            continue
        if cid in seen:
            errors.append(f"duplicate concept_id: {cid}")
        seen.add(cid)
        if not c.get("canonical_name"):
            errors.append(f"concept {cid} missing canonical_name")
        if not c.get("aliases"):
            warnings.append(f"concept {cid} has no aliases")
        for ref_key in ("parent_concepts", "child_concepts",
                        "related_concepts"):
            for ref in c.get(ref_key) or []:
                if ref not in ids:
                    errors.append(f"concept {cid} references missing concept: {ref}")

    for m in data.get("metrics", []):
        if not isinstance(m, dict):
            errors.append("metric entry is not an object")
            continue
        name = m.get("metric_name")
        if not name:
            errors.append("metric missing metric_name")
            continue
        if not m.get("canonical_definition"):
            errors.append(f"metric {name} missing canonical_definition")
        if not m.get("aliases"):
            warnings.append(f"metric {name} has no aliases")

    return errors, warnings


def seed(store: KBStore, data: dict[str, Any]) -> dict[str, int]:
    """Idempotent upsert of concepts + metrics_ontology. Returns row counts."""
    counts = {"concepts": 0, "metrics_ontology": 0}

    cols = "concept_id, " + ", ".join(_CONCEPT_FIELDS)
    placeholders = ", ".join("?" * (len(_CONCEPT_FIELDS) + 1))
    updates = ", ".join(f"{f}=excluded.{f}" for f in _CONCEPT_FIELDS)
    for c in data.get("concepts", []):
        values = [c.get("concept_id")] + [
            _j(c.get(f)) if f in _JSON_FIELDS else c.get(f) for f in _CONCEPT_FIELDS
        ]
        store.conn.execute(
            f"INSERT INTO concepts ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(concept_id) DO UPDATE SET {updates}",
            values,
        )
        counts["concepts"] += 1

    mcols = "metric_name, " + ", ".join(_METRIC_FIELDS)
    mplaceholders = ", ".join("?" * (len(_METRIC_FIELDS) + 1))
    mupdates = ", ".join(f"{f}=excluded.{f}" for f in _METRIC_FIELDS)
    for m in data.get("metrics", []):
        values = [m.get("metric_name")] + [
            _j(m.get(f)) if f in _JSON_FIELDS else m.get(f) for f in _METRIC_FIELDS
        ]
        store.conn.execute(
            f"INSERT INTO metrics_ontology ({mcols}) VALUES ({mplaceholders}) "
            f"ON CONFLICT(metric_name) DO UPDATE SET {mupdates}",
            values,
        )
        counts["metrics_ontology"] += 1

    store.conn.commit()
    return counts


def has_concepts(store: KBStore) -> bool:
    return store.conn.execute("SELECT 1 FROM concepts LIMIT 1").fetchone() is not None


def alias_map(store: KBStore) -> dict[str, list[str]]:
    """alias -> [canonical_name, child canonical_names] for query expansion."""
    rows = store.conn.execute("SELECT * FROM concepts").fetchall()
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        for f in ("aliases", "parent_concepts", "child_concepts",
                  "related_concepts", "related_methods", "related_papers"):
            d[f] = _jload(d.get(f)) or []
        by_id[d["concept_id"]] = d

    out: dict[str, list[str]] = {}
    for c in by_id.values():
        children = [
            by_id[ch]["canonical_name"] for ch in c.get("child_concepts", [])
            if ch in by_id
        ]
        terms = [c["canonical_name"]] + children
        for alias in c.get("aliases", []):
            out[alias.lower()] = terms
    return out
