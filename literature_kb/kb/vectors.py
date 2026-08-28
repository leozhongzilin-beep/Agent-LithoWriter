"""Vector embeddings + cosine search (PRD KB-Completion group B).

Embeddings live in the existing `embeddings` table as numpy BLOBs; retrieval is
brute-force cosine over the row batch (numpy — no FAISS; fine at personal-KB
scale). `Embedder` is injected, so everything here is testable offline with a
fake embedder and depends only on the protocol in `embedder.py`.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import numpy as np

from . import chunker
from .embedder import Embedder
from .store import KBStore


def cosine(u: np.ndarray, v: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    un = np.linalg.norm(u)
    vn = np.linalg.norm(v)
    if un == 0 or vn == 0:
        return 0.0
    return float(np.dot(u, v) / (un * vn))


def store_embeddings(
    store: KBStore,
    embedder: Embedder,
    paper_id: str,
    object_type: str,
    items: list[tuple[str, str]],
) -> int:
    """Embed (object_id, text) pairs and replace the paper's rows for a type."""
    if not items:
        return 0
    mat = embedder.embed([t for _, t in items])
    with store.conn:
        store.conn.execute(
            "DELETE FROM embeddings WHERE paper_id = ? AND object_type = ?",
            (paper_id, object_type),
        )
        for (oid, _text), vec in zip(items, mat, strict=True):
            store.conn.execute(
                "INSERT INTO embeddings (paper_id, object_type, object_id, "
                "model, model_version, vector, created_at) VALUES (?,?,?,?,?,?,?)",
                (paper_id, object_type, oid, embedder.model_name,
                 getattr(embedder, "model_version", ""),
                 vec.astype(np.float32).tobytes(), _ts()),
            )
    return len(items)


def search_vectors(
    store: KBStore,
    embedder: Embedder,
    query_text: str,
    object_type: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Brute-force cosine search over one object type, best-first."""
    qv = embedder.embed([query_text])[0]
    rows = store.conn.execute(
        "SELECT paper_id, object_id, vector FROM embeddings WHERE object_type = ?",
        (object_type,),
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for r in rows:
        vec = np.frombuffer(r["vector"], dtype=np.float32)
        s = cosine(qv, vec)
        scored.append({"paper_id": r["paper_id"], "object_id": r["object_id"],
                       "score": s})
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]


def embed_paper(
    store: KBStore, embedder: Embedder, paper_id: str
) -> dict[str, int]:
    """Embed a paper's L0 card, evidence blocks, formulas and chunks."""
    paper = store.get_paper(paper_id)
    if paper is None:
        return {}
    counts: dict[str, int] = {}

    l0_text = " ".join(x for x in [
        paper.get("title") or "", paper.get("one_line_description") or "",
        " ".join(paper.get("keywords") or []),
    ] if x)
    if l0_text:
        counts["paper"] = store_embeddings(
            store, embedder, paper_id, "paper", [("L0", l0_text)])

    ev_rows = store.conn.execute(
        "SELECT evidence_id, source_text FROM paper_evidence WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    if ev_rows:
        counts["evidence"] = store_embeddings(
            store, embedder, paper_id, "evidence",
            [(r["evidence_id"], r["source_text"]) for r in ev_rows])

    fm_rows = store.conn.execute(
        "SELECT formula_id, formula_latex, semantic_description "
        "FROM formulas WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    if fm_rows:
        counts["formula"] = store_embeddings(
            store, embedder, paper_id, "formula",
            [(r["formula_id"],
              f"{r['formula_latex']} {r['semantic_description'] or ''}")
             for r in fm_rows])

    chunks = chunker.get_chunks(store, paper_id)
    if chunks:
        counts["chunk"] = store_embeddings(
            store, embedder, paper_id, "chunk",
            [(c["chunk_id"], c["text"]) for c in chunks])

    return counts


def _ts() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat(timespec="seconds")
