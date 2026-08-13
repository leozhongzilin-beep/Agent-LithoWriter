"""Formula search (spec §8). Keyword over latex/semantic/variables + role filter."""

from __future__ import annotations

from typing import Any

from . import fts, relevance
from .contract import FormulaHit
from .store import KBStore


def search_formulas(
    store: KBStore,
    query: str,
    *,
    role: str | None = None,
    limit: int = 10,
) -> list[FormulaHit]:
    """Formulas whose latex/semantic/variables match, optionally by role."""
    hits = fts.query(store.conn, "fts_formulas", query, limit=limit)
    fids = [h["formula_id"] for h in hits]
    scores = {h["formula_id"]: h["score"] for h in hits}

    if not fids and query.strip():
        q = f"%{query}%"
        rows = store.conn.execute(
            "SELECT formula_id FROM formulas WHERE formula_latex LIKE ? "
            "OR semantic_description LIKE ? LIMIT ?",
            (q, q, limit),
        ).fetchall()
        fids = [r["formula_id"] for r in rows]

    if not fids:
        return []

    placeholders = ", ".join("?" * len(fids))
    rows_by_id: dict[str, dict[str, Any]] = {}
    for r in store.conn.execute(
        f"SELECT * FROM formulas WHERE formula_id IN ({placeholders})", fids
    ).fetchall():
        rows_by_id[r["formula_id"]] = dict(r)

    if role:
        fids = [f for f in fids if rows_by_id[f].get("formula_role") == role]
        if not fids:
            return []

    norm = relevance.normalize([scores.get(f, 0.0) for f in fids])
    out: list[FormulaHit] = []
    for fid, rel in zip(fids, norm):
        row = rows_by_id[fid]
        out.append(FormulaHit(
            formula_id=fid,
            paper_id=row["paper_id"],
            formula_latex=row.get("formula_latex") or "",
            formula_role=row.get("formula_role") or "",
            semantic_description=row.get("semantic_description") or "",
            variables=_variables_for(store, fid),
            source_evidence_id=row.get("source_evidence_id") or "",
            relevance=rel,
        ))
    out.sort(key=lambda f: f.relevance, reverse=True)
    return out[:limit]


def _variables_for(store: KBStore, formula_id: str) -> list[dict[str, str]]:
    return [
        {"symbol": r["symbol"], "meaning": r["meaning"], "unit": r["unit"]}
        for r in store.conn.execute(
            "SELECT symbol, meaning, unit FROM formula_variables "
            "WHERE formula_id = ? ORDER BY variable_id",
            (formula_id,),
        ).fetchall()
    ]
