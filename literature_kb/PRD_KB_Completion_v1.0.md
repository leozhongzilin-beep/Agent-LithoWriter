# PRD: Literature KB Completion — L4 Chunking + Embeddings + Ontology + CSL v1.0

- **Status**: `implemented` (2026-08-13 — 142 tests, ruff clean; no issue tracker configured, this file is canonical until pushed to `github.com/leozhongzilin-beep/Agent-LithoWriter`)
- **Depends on**: storage milestone (112 tests) + retrieval milestone (`PRD_Retrieval_Router_v1.0.md`, implemented). All target tables already exist in the schema — this milestone adds tools, content, and wiring, **no structural DDL changes**.
- **Explicitly NOT covered**: writing-agent pipeline integration — that is a separate project at `D:\codesforOpenCode\writing-agent` (the KB was moved under it; integration PRD is out of scope here).

---

## Problem Statement

The KB can store and retrieve (L0–L3, formulas, citations), but four capabilities are inert:

1. **L4 is a pointer, not content** — `paper_fulltext.chunks` stays empty, so the router's "escalate to L4" path has nothing to return when L3 evidence is insufficient.
2. **No semantic retrieval** — search is lexical (FTS5 BM25) only; synonyms and domain phrasing ("learning-based mask optimization" vs "AI-ILT") never match.
3. **Ontology tables are empty** — concept expansion and metric-comparability rules exist as hooks that never fire.
4. **Citations are minimal** — `resolve_citation` returns a hand-rolled `(Author, Year)` fallback; no real per-journal rendering.

The result: the writing agent still can't get full-text paragraphs, synonym-aware search, domain-aware query expansion, or journal-formatted references from the KB.

## Solution

Four cohesive feature groups that complete the KB's self-contained capability:

- **L4 chunking**: parse an archived source document into sections/paragraph chunks stored in `paper_fulltext` — making the router's L4 escalation real.
- **Embeddings + hybrid retrieval**: a pluggable local `Embedder` (sentence-transformers), chunk/evidence-level embeddings in SQLite BLOB, numpy brute-force cosine search (no FAISS), and a `vector` term fused into the hybrid relevance score.
- **Ontology seeding**: curated `concepts` + `metrics_ontology` seed content with an idempotent loader/validator; the existing concept-expansion hook activates.
- **CSL rendering**: a citeproc-py wrapper that renders full per-journal bibliographies and in-text citations from canonical records; `resolve_citation` becomes cache → CSL → minimal-fallback.

Each group is independently implementable; all share the existing store/schema/retrieval infrastructure and the established `tmp_kb` + `make_package` test seam.

## User Stories

**Research writing agent:**

1. As a research writing agent, I want a `TECHNICAL` query that exhausts L3 to escalate into real L4 paragraphs (section-titled chunks), so that full-text context is available on demand.
2. As a research writing agent, I want a `DISCOVERY` query to match semantically ("AI-ILT" finds "learning-based mask optimization" papers), so that recall doesn't depend on exact wording.
3. As a research writing agent, I want every `ResultItem` to carry a real, style-appropriate rendered citation (e.g. Nature or IEEE), so that I can drop references into a draft unchanged.
4. As a research writing agent, I want `resolve_citation(paper_id, style)` to prefer a CSL-rendered entry, so that journal-format rendering is reproducible and style-extensible.
5. As a research writing agent, I want hybrid ranking (BM25 + vector) to surface semantically-close papers even when they share no lexical tokens, so that related-work coverage improves.

**Literature review agent:**

6. As a literature review agent, I want to render a full numbered bibliography for a set of papers in one style via one call, so that a review's reference list is generated, not hand-assembled.
7. As a literature review agent, I want concept expansion to activate with a seeded ontology (AI-ILT → deep-learning ILT → CNN/Transformer), so that broad surveys hit the taxonomy's children/aliases.
8. As a literature review agent, I want metric comparability rules from the metrics ontology to flag when two papers' numbers should not be ranked together, so that reviews don't fabricate comparisons.

**Experiment planning agent:**

9. As an experiment planning agent, I want L4 paragraphs of the method section retrievable as evidence, so that I can reproduce an experimental setup from the source text.
10. As an experiment planning agent, I want evidence-level vector search, so that a qualitative question ("which paper discusses runtime bottlenecks?") finds the right paragraphs even with unusual phrasing.

**Citation manager:**

11. As a citation manager, I want a style registry (Nature / IEEE / OLT / custom) loaded from the `citation_styles` table, so that adding a journal = adding a `.csl` file.
12. As a citation manager, I want `generated: true` to remain only for the minimal fallback, so that CSL-rendered entries are never mistaken for hand-rolled ones.

**Researcher / KB operator:**

13. As a researcher, I want `kb chunk <paper_id>` to parse an archived source into `paper_fulltext`, so that I can materialize L4 for already-imported papers.
14. As a researcher, I want `kb embed` to batch-embed the whole KB (backfill), so that existing papers become vector-searchable without re-import.
15. As a researcher, I want `kb seed-ontology` to load curated concepts/metrics idempotently, so that re-running never duplicates rows.
16. As a researcher, I want `kb bibliography <style>` to print a full reference list, so that I can verify rendering without writing code.
17. As a researcher, I want embeddings stored locally as BLOBs with brute-force cosine (no external service, no FAISS), so that vector search works offline and stays dependency-light.
18. As a researcher, I want a configurable embedding model with a pluggable `Embedder` protocol, so that I can swap BGE/other models without touching retrieval code.
19. As a researcher, I want the chunker and embedder to degrade gracefully (unsupported source → clear message; model missing → skip vector strategy), so that partial environments never crash.

## Implementation Decisions

### Feature Group A — L4 paragraph chunking

New deep module `chunker.py`:

- `ChunkDoc` shape (decision-rich, from prototype planning):
  ```text
  ChunkDoc { paper_id, sections: [str], chunks: [Chunk] }
  Chunk    { chunk_id, section, paragraph_index, text, page? }
  ```
- `chunk_source(store, paper_id, source_path) -> ChunkDoc | None`: format detection by extension/content — markdown/text/XML/LaTeX now; PDF deferred to a PyMuPDF-backed reader (a follow-on within this group).
- Section detection: markdown `#`/`##` headers, LaTeX `\section`/`\subsection`, XML `<sec>`; paragraphs = blank-line-separated blocks. References/boilerplate sections are kept (they are evidence) but not specially treated.
- `store_chunks(store, paper_id, doc)`: writes `paper_fulltext.chunks` (JSON), `section_index`, sets `chunk_available = 1`. Idempotent — re-chunking replaces.
- Wiring: the retrieval router's L4 escalation target becomes meaningful (returns chunk texts); a CLI `kb chunk` runs it on the archived source.

### Feature Group B — embeddings + hybrid retrieval

- New deep module `embedder.py`: an `Embedder` protocol (`embed(texts) -> np.ndarray`), a default local implementation over `sentence-transformers` with lazy model load, and a factory reading the configured model id (default `BAAI/bge-small-zh-v1.5`, bilingual — matches this mixed zh/en corpus). Missing model → `Embedder` raises a clear error that retrieval handles by skipping the vector strategy.
- New deep module `vectors.py`: embeddings stored in the existing `embeddings` table as numpy BLOBs (object_type: `paper` | `evidence` | `formula` | `chunk`); brute-force cosine over a row batch (numpy), no FAISS, `vectors/` dir stays reserved. `search_vectors(store, query_vec, object_type, limit)`.
- Import-time embedding: extend the `post_insert` hook in the write path to also write paper/evidence/formula embeddings (composed with the existing FTS sync); a separate `kb embed` command backfills an existing KB.
- Hybrid fusion: the router's `search_l0`/`search_l3`/`search_formulas` gain an optional query-vector term; relevance composes `w_b·bm25_norm + w_v·vector_norm + w_r·recency + w_m·metadata + w_c·centrality`. When no vector term is available the existing weights stand — the vector term is additive and zero-weighted in its absence.

### Feature Group C — ontology seeding + concept expansion activation

- New deep module `ontology.py`:
  - `load_seed(path)` → records (concepts: id/canonical/aliases/parents/children; metrics: name/definition/unit/category/comparability_rules/pitfalls).
  - `validate(records)` → errors on orphan parent/child refs, duplicate ids, empty aliases.
  - `seed(store, concepts, metrics)` → idempotent upsert (replace by id), no duplicates on re-run.
  - `alias_map(store) -> {alias: [canonical tokens]}` feeding the retrieval expansion hook.
- Seed content ships as data files in a `seeds/` directory: an ILT concept taxonomy (from spec §9's example — AI-ILT → deep-learning ILT → CNN/Transformer/neural mask synthesis) and a metric ontology (EPE/CD/PVBand/TAT/shots with comparability rules, e.g. "TAT ≠ inference time").
- Activation: `RetrievalService.retrieve` populates the existing `aliases` parameter from `alias_map(store)` (currently callers may pass it; now the facade does it). `get_structured_results` surfaces `comparability_rules` as a validation note per metric where the ontology defines them.

### Feature Group D — CSL citation rendering

- New deep module `csl.py` wrapping `citeproc-py`:
  - `load_style(style_id)`: resolution order `citation_styles` table → bundled `.csl` directory; style loaded once and cached.
  - `render_bibliography(store, paper_ids, style_id) -> list[str]` (numbered entries from canonical `bibliographic_record`).
  - `render_in_text(store, paper_id, style_id) -> str`.
- Style registry: seed `citation_styles` with Nature / IEEE / OLT (Optics & Laser Technology) + a bundled `csl/` directory; adding a journal = adding a `.csl` row + file.
- `resolve_citation` upgrade — truth order preserved: `citation_records` cache → CSL render from the canonical record → minimal `(Author, Year)` fallback marked `generated: true`.
- CLI `kb bibliography <style_id>` prints a full rendered reference list; `kb cite` returns real rendered entries when a style is resolvable.

### Cross-cutting

- **No structural schema changes**: all target tables (`paper_fulltext`, `embeddings`, `citation_styles`, `concepts`, `metrics_ontology`) exist from the storage milestone; this milestone adds tools, content, and wiring only.
- **Dependencies added**: `sentence-transformers` (+ existing torch), `citeproc-py`; PDF reading deferred (`PyMuPDF`). All optional at runtime — missing model/library degrades to the existing lexical path, never crashes.
- **Self-containment preserved**: no dependency on `codes/pdf2md` or the `ILT project/` vault; chunking/embedding operate on sources archived inside the KB.

## Testing Decisions

- **What a good test is**: external behavior only — given a source document / a seeded KB / a citation record, the public functions return correct chunks, ranked vector hits, seeded rows, and rendered citations. No assertions on private internals; tests survive the implementation changing entirely.
- **Modules tested (all — per the retrieval milestone's "all modules" precedent)**:
  - Pure/deep: `chunker` (text→ChunkDoc, section/paragraph splitting, idempotent re-store), `vectors` (cosine ranking, BLOB round-trip), `ontology` (validator rejects orphan refs; seed idempotent), `csl` (style loading, bibliography ordering; skipped via `pytest.importorskip` when citeproc-py absent).
  - DB-backed: `store_chunks` / `chunk_available` flip, `store_embeddings`/`search_vectors` over `tmp_kb`, ontology `seed` re-run, `resolve_citation` cache→CSL→fallback ordering.
  - Integration: import-time embedding sync (post_insert), hybrid score including the vector term, `RetrievalService.retrieve` with the ontology-activated alias map.
- **Prior art**: the existing `tmp_kb` fixture + `make_package` factory and the vertical-slice TDD pattern from the storage/retrieval milestones (112 passing tests) are the template for every test here.

## Out of Scope

- **Writing-agent pipeline integration** — a separate project/PRD at `D:\codesforOpenCode\writing-agent`.
- **FAISS / ANN indexes** — brute-force numpy cosine over a personal-scale KB is sufficient; swap later if the KB grows orders of magnitude.
- **PDF-native chunking** — markdown/text/XML/LaTeX first; PyMuPDF reader is a follow-on within group A.
- **Embedding quality tuning** — model choice is configurable; per-domain fine-tuning is out of scope.
- **Ontology content depth** — an initial curated taxonomy + metric rules ship; maintaining it is an ongoing data task, not part of this milestone.
- **CSL style authoring** — bundling/reference styles only; authoring new `.csl` files is out of scope.
- **The `paper_to_literature_kb` Skill implementation** — this milestone consumes its output package shape only.

## Further Notes

- **Env facts (verified 2026-08-13)**: FTS5 available in stdlib sqlite3; torch already present for ML research so `sentence-transformers` adds little marginal footprint; `citeproc-py` is the standard Python CSL processor.
- **Frozen invariants carried forward**: five-way identity separation, KB fully self-contained/decoupled, `processing_jobs` = audit log, no-fabrication (every returned number keeps `source_evidence_id`). Embeddings/CSL/chunking must not weaken any of these.
- **Token budgets unchanged**: vector and L4 content feed the existing progressive router and per-layer budgets from the retrieval milestone.
- **Naming**: combined PRD chosen deliberately — the four groups share schema, test fixtures, and the retrieval layer; each group remains independently implementable (slice by group in implementation).
