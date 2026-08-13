"""Tests for the L0/L1/L2 searchers (kb/search.py)."""

from __future__ import annotations

from typing import ClassVar

from kb import chunker
from kb.contract import ResultItem
from kb.importtool import import_package
from kb.search import (
    get_paper_card,
    get_structured_results,
    search_l0,
    search_l1,
    search_l2,
    search_l4,
)


def _import_variant(tmp_kb, make_package, *, title, doi, metric_name="EPE",
                    metric_value=2.1, domain="ILT", year=2024):
    pkg = make_package()
    L0 = pkg["paper"]["L0"]
    L0["title"] = title
    L0["doi"] = doi
    L0["domain_tags"] = [domain]
    L0["year"] = year
    m = pkg["paper"]["L2"]["metrics"][0]
    m["name"] = metric_name
    m["value"] = metric_value
    m["condition"] = {"dataset": "MetalSet", "pitch": 45}
    pkg["paper"]["L3"]["evidence"][0]["source_text"] = (
        f"The proposed method achieves {metric_name} of {metric_value} nm on MetalSet."
    )
    pkg["citation_records"] = []
    return import_package(tmp_kb, pkg)


def _seed_two(tmp_kb, make_package):
    litho = _import_variant(
        tmp_kb, make_package,
        title="Deep Learning for Inverse Lithography",
        doi="10.1016/x1", metric_name="EPE")
    other = _import_variant(
        tmp_kb, make_package,
        title="Battery Charging Circuit Optimization",
        doi="10.1016/x2", metric_name="Runtime", domain="SMO")
    return litho.paper_id, other.paper_id


def test_l0_discovery_returns_ranked_result_items(tmp_kb, make_package):
    litho_id, _ = _seed_two(tmp_kb, make_package)
    results = search_l0(tmp_kb, "lithography")
    assert isinstance(results, list)
    assert all(isinstance(r, ResultItem) for r in results)
    assert results[0].paper_id == litho_id
    top = results[0]
    assert top.citation_key  # Zhang2024...
    assert "Zhang" in top.citation and "2024" in top.citation
    assert "L0" in top.available_levels and "L3" in top.available_levels
    assert top.evidence_ids  # traceable evidence ids present


def test_l0_metadata_filter_restricts_domain(tmp_kb, make_package):
    litho_id, smo_id = _seed_two(tmp_kb, make_package)
    results = search_l0(tmp_kb, "", filters={"domain": "SMO"})
    pids = {r.paper_id for r in results}
    assert pids == {smo_id}
    assert litho_id not in pids


def test_l0_year_filter(tmp_kb, make_package):
    old = _import_variant(tmp_kb, make_package, title="Old Litho Paper",
                          doi="10.1016/x1", year=2015)
    new = _import_variant(tmp_kb, make_package, title="New Litho Paper",
                          doi="10.1016/x2", year=2024)
    results = search_l0(tmp_kb, "", filters={"year_from": 2020})
    pids = {r.paper_id for r in results}
    assert new.paper_id in pids
    assert old.paper_id not in pids


def test_l0_like_fallback_when_fts_misses(tmp_kb):
    # a paper row written WITHOUT FTS sync (drift / pre-FTS KB) is still found
    tmp_kb.conn.execute(
        "INSERT INTO papers (paper_id, title, citation_key, created_at, updated_at) "
        "VALUES ('ILT_2024_001', 'Unique Fallback Term ILT', 'K1', 'x', 'x')"
    )
    tmp_kb.conn.commit()
    results = search_l0(tmp_kb, "unique fallback")
    assert [r.paper_id for r in results] == ["ILT_2024_001"]


def test_get_paper_card_returns_l1(tmp_kb, make_package):
    pid, _ = _seed_two(tmp_kb, make_package)
    card = get_paper_card(tmp_kb, pid)
    assert card["paper_id"] == pid
    assert card["research_problem"] == "ILT is expensive."
    assert card["recommended_use"]["method"] == "strong"
    assert card["title"]  # joined from papers


def test_search_l1_technical(tmp_kb, make_package):
    litho_id, _ = _seed_two(tmp_kb, make_package)
    results = search_l1(tmp_kb, "inverse lithography")
    assert results[0].paper_id == litho_id
    assert results[0].key_fact  # card summary as key fact


def test_search_l2_metric(tmp_kb, make_package):
    litho_id, _ = _seed_two(tmp_kb, make_package)
    results = search_l2(tmp_kb, "EPE")
    assert results and results[0].paper_id == litho_id
    assert "EPE" in results[0].key_fact


class ManualEmbedder:
    """Text->vector map controlling similarity explicitly (fully offline)."""
    model_name = "manual"
    d: ClassVar[dict[str, list[float]]] = {
        "lithography": [1.0, 0.0],
        "lithography paper": [0.95, 0.05],
        "mask synthesis paper": [0.8, 0.3],
    }

    def embed(self, texts):
        import numpy as np
        return np.array(
            [self.d.get(t, [0.0, 0.0]) for t in texts], dtype=np.float32)


def test_search_l0_hybrid_unions_vector_candidates(tmp_kb, make_package):
    """A paper absent lexically is rescued by a close vector embedding."""
    from kb import vectors

    pa = make_package()
    pa["paper"]["L0"]["title"] = "Deep Learning for Inverse Lithography"
    pa["paper"]["L0"]["doi"] = "10.1016/xa"
    pa["citation_records"] = []
    a = import_package(tmp_kb, pa).paper_id

    pb = make_package()
    pb["paper"]["L0"]["title"] = "Learning-Based Mask Synthesis"
    pb["paper"]["L0"]["doi"] = "10.1016/xb"
    pb["citation_records"] = []
    b = import_package(tmp_kb, pb).paper_id

    emb = ManualEmbedder()
    vectors.store_embeddings(tmp_kb, emb, a, "paper", [("L0", "lithography paper")])
    vectors.store_embeddings(tmp_kb, emb, b, "paper", [("L0", "mask synthesis paper")])

    results = search_l0(tmp_kb, "lithography", embedder=emb)
    pids = [r.paper_id for r in results]
    assert a in pids   # lexical match, ranked via bm25 + vector
    assert b in pids   # vector-only match rescued (no lexical "lithography")
    assert pids.index(a) < pids.index(b)


def test_structured_results_surfaces_comparability_rules(tmp_kb, make_package):
    from kb import ontology

    ontology.seed(tmp_kb, ontology.load_seed())  # metrics_ontology has EPE rules
    pid, _ = _seed_two(tmp_kb, make_package)
    structured = get_structured_results(tmp_kb, pid)
    m = structured["metrics"][0]
    assert m["name"] == "EPE"
    assert m.get("comparability_rules")
    assert "lithography condition" in m["comparability_rules"]
    assert m.get("common_pitfalls")


def test_l4_only_available_after_chunking(tmp_kb, make_package):
    """L4 must mean 'chunks exist', not merely 'a fulltext row exists'."""
    res = import_package(tmp_kb, make_package())
    before = search_l0(tmp_kb, "lithography")[0]
    assert "L4" not in before.available_levels

    chunker.store_chunks(tmp_kb, res.paper_id, chunker.chunk_markdown(
        "# T\n\nIntro.\n\n## Method\n\nDetail paragraph.\n", res.paper_id))
    after = search_l0(tmp_kb, "lithography")[0]
    assert "L4" in after.available_levels


def test_search_l4_finds_chunks_by_token(tmp_kb, make_package):
    res = import_package(tmp_kb, make_package())
    chunker.store_chunks(tmp_kb, res.paper_id, chunker.chunk_markdown(
        "# T\n\nIntro paragraph.\n\n## Method\n\n"
        "The predictor reduces turnaround time on MetalSet.\n", res.paper_id))
    hits = search_l4(tmp_kb, "turnaround")
    assert hits and hits[0].paper_id == res.paper_id
    assert "turnaround" in hits[0].key_fact
    assert hits[0].available_levels == ["L4"]
    assert hits[0].best_use.startswith("L4 chunk")  # section surfaced


def test_search_l4_restricts_paper(tmp_kb, make_package):
    a = import_package(tmp_kb, make_package())
    chunker.store_chunks(tmp_kb, a.paper_id, chunker.chunk_markdown(
        "# T\n\nTurnaround detail.\n", a.paper_id))
    assert search_l4(tmp_kb, "turnaround", paper_ids=["ILT_9999_999"]) == []
    assert search_l4(tmp_kb, "turnaround", paper_ids=[a.paper_id])


def test_get_structured_results_keeps_conditions(tmp_kb, make_package):
    pid, _ = _seed_two(tmp_kb, make_package)
    structured = get_structured_results(tmp_kb, pid)
    assert structured["metrics"]
    m = structured["metrics"][0]
    assert m["name"] == "EPE"
    assert m["condition"]["dataset"] == "MetalSet"
    assert m["source_evidence_id"]  # no fabrication: every number is traceable
