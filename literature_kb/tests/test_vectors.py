"""Tests for vector embeddings + cosine search (kb/vectors.py)."""

from __future__ import annotations

from typing import ClassVar

import numpy as np
from kb import vectors
from kb.importtool import import_package


class FakeEmbedder:
    """Deterministic token-presence embedder — no model, fully offline."""

    model_name = "fake"
    vocab: ClassVar[list[str]] = ["ilt", "mask", "lithography", "kan", "runtime",
                                  "epe", "battery", "charging", "loss", "forward"]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            v = np.zeros(len(self.vocab), dtype=np.float32)
            tl = t.lower()
            for i, tok in enumerate(self.vocab):
                if tok in tl:
                    v[i] = 1.0
            out.append(v)
        return np.array(out, dtype=np.float32)


def _insert_paper(tmp_kb, pid, title):
    tmp_kb.conn.execute(
        "INSERT INTO papers (paper_id, title, citation_key, created_at, updated_at) "
        "VALUES (?,?,?,?,?)", (pid, title, f"K{pid}2024", "x", "x"))
    tmp_kb.conn.commit()


def _store_ilv_vs_battery(tmp_kb):
    _insert_paper(tmp_kb, "ILT_2024_001", "ILT mask paper")
    _insert_paper(tmp_kb, "SMO_2025_001", "battery paper")
    emb = FakeEmbedder()
    vectors.store_embeddings(tmp_kb, emb, "ILT_2024_001", "paper",
                             [("L0", "mask optimization for inverse ILT lithography")])
    vectors.store_embeddings(tmp_kb, emb, "SMO_2025_001", "paper",
                             [("L0", "battery charging circuit")])
    return emb


def test_search_vectors_ranks_by_cosine(tmp_kb):
    emb = _store_ilv_vs_battery(tmp_kb)
    hits = vectors.search_vectors(tmp_kb, emb, "ILT mask", "paper")
    assert hits[0]["paper_id"] == "ILT_2024_001"
    assert hits[0]["score"] > hits[1]["score"]
    assert hits[1]["paper_id"] == "SMO_2025_001"


def test_search_vectors_scoped_to_type(tmp_kb):
    emb = _store_ilv_vs_battery(tmp_kb)
    vectors.store_embeddings(tmp_kb, emb, "ILT_2024_001", "evidence",
                             [("ev1", "the EPE is 2.1 nm")])
    # an evidence-type search finds the EPE evidence block
    ev_hits = vectors.search_vectors(tmp_kb, emb, "EPE", "evidence")
    assert ev_hits and ev_hits[0]["object_id"] == "ev1"
    # a paper-type search never leaks evidence objects
    paper_hits = vectors.search_vectors(tmp_kb, emb, "EPE", "paper")
    assert all(h["object_id"] != "ev1" for h in paper_hits)


def test_embed_paper_writes_objects(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    counts = vectors.embed_paper(tmp_kb, FakeEmbedder(), "ILT_2024_001")
    assert counts["paper"] == 1
    assert counts["evidence"] == 1
    assert counts["formula"] == 1
    types = {r["object_type"] for r in tmp_kb.conn.execute(
        "SELECT object_type FROM embeddings WHERE paper_id='ILT_2024_001'"
    ).fetchall()}
    assert types == {"paper", "evidence", "formula"}


def test_embed_paper_is_replace_not_append(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    vectors.embed_paper(tmp_kb, FakeEmbedder(), "ILT_2024_001")
    vectors.embed_paper(tmp_kb, FakeEmbedder(), "ILT_2024_001")
    n = tmp_kb.conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE paper_id='ILT_2024_001'"
    ).fetchone()[0]
    assert n == 3  # paper + evidence + formula, not duplicated


def test_cosine_of_identical_is_one():
    u = np.array([1.0, 0.0, 1.0], dtype=np.float32)
    assert vectors.cosine(u, u) == pytest_approx(1.0)


def pytest_approx(x):
    import pytest
    return pytest.approx(x)
