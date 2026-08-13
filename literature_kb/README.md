# Literature Knowledge Base

Self-contained storage layer for the hierarchical literature KB described in
`writing-agent/Literature_Knowledge_Base_RAG_Spec_v1.0.md`. This milestone is
**storage only** — directories, schema, and import tools. Retrieval (progressive
L0→L4, hybrid search) is the next milestone.

The KB is **fully self-contained and deliberately decoupled** from any external
paper vault (e.g. `ILT project/raw/`). Every ingested source is copied into the
KB; it is the writing-agent's sole data source.

## Layout

```
literature_kb/
├── kb/                    # Python package — the tools
│   ├── schema.py          # SQLite DDL (all 18 collections + sequences)
│   ├── store.py           # KBStore: counters, reads, atomic write, archive
│   ├── package.py         # canonical package: load / validate / normalize
│   ├── importtool.py      # kb add: identity ladder + change detection
│   ├── ids.py             # paper_id / citation_key / sub-id generators
│   ├── cli.py             # kb init | add | list | status
│   └── config.py          # KB_ROOT resolution
├── data/                  # KB_ROOT (default; override with $KB_ROOT or --root)
│   ├── kb.db              # SQLite (WAL, FK on)
│   ├── raw/<paper_id>/    #   package.json + manifest.json + source/
│   └── vectors/           # reserved for the embedding index (later)
└── tests/
```

## Identity model (frozen)

| field | role | example |
|-------|------|---------|
| `paper_id` | human-readable internal identity | `ILT_2024_031` |
| `doi` | external canonical identity (unique) | `10.1016/...` |
| `source_hash` | integrity / change detection | `sha256:...` |
| `citation_key` | writing-agent internal reference | `Zhang2024DeepLearning` |
| `bibtex_key` | BibTeX artifact key (separate!) | `zhang2024deepilt` |

These five are **distinct and never merged**.

## Usage

```bash
pip install -r requirements.txt

python -m kb init                                    # create dirs + schema + FTS index
python -m kb add package.json [--source paper.pdf]   # import one paper (instantly searchable)
python -m kb add package.yaml --paper-id SMO_2025_017
python -m kb list
python -m kb status

# Retrieval (read side)
python -m kb search "KAN mask" --intent DISCOVERY --filter domain=ILT --filter year_from=2020
python -m kb search "EPE" --intent RESULT
python -m kb search "The method reduces TAT" --intent VERIFICATION
python -m kb get-card ILT_2024_031            # L1
python -m kb metrics ILT_2024_031 --metric EPE   # L2 (conditions + evidence kept)
python -m kb verify "KAN reduces TAT by 5x"    # L3 claim check
python -m kb formula "litho forward model" --role forward_model
python -m kb cite ILT_2024_031 --style ieee    # cache -> CSL -> minimal fallback marked generated

# KB completion (L4 / embeddings / ontology / CSL)
python -m kb add package.json --embed                # import + embed in one step
python -m kb chunk ILT_2024_031 --source paper.md    # materialize L4 paragraphs
python -m kb chunks ILT_2024_031                     # read the chunks
python -m kb embed                                    # vector-embed the whole KB
python -m kb seed-ontology                            # load curated concepts + metrics
python -m kb bibliography author-date                 # full CSL reference list
```

L4 is now a real retrieval target: `RetrievalService.l4(query)` searches the
chunk index (FTS-synced by `kb chunk`), the router's escalation chains reach
L4, and `available_levels` only advertises L4 once chunks exist. With the
metrics ontology seeded, `get_structured_results` also surfaces each metric's
`comparability_rules` / `common_pitfalls`.

`KB_ROOT` precedence: `--root` flag > `$KB_ROOT` env var > `<literature_kb>/data`.

## Retrieval Router (read side)

`RetrievalService.retrieve(query, intent, filters?, max_tokens?)` is the single
facade the writing agent calls. It routes the intent (spec §13.1) to the right
layer and search strategy, ranks by hybrid relevance, truncates to the token
budget, and returns a structured `ResultSet` (§15) — never raw text.

| intent | start layer | returns |
|--------|------------|---------|
| `DISCOVERY` | L0 | ranked L0 index cards (title, citation, citation_key, relevance, evidence ids) |
| `CITATION` | L0 | paper resolved by citation_key / DOI / paper_id + rendered in-text citation |
| `TECHNICAL` | L1 | papers with L1 card summary as key fact |
| `RESULT` | L2 | papers with matching metric, conditions preserved |
| `FORMULA` | Formula KB | formulas with role + variable meanings |
| `VERIFICATION` | L3 | evidence hits with trace (paper → section → page) |
| `COMPARISON` | L2 | metric cards + comparison validity |

Ranking: `0.55·BM25 + 0.20·recency + 0.15·metadata + 0.10·citation-graph centrality`
(FTS5 `bm25()`, stdlib — no new deps). `verify_claim` never upgrades evidence:
no evidence → `unverified`; claim strength (A/B/C/D) is surfaced only when a
stored claim shares tokens with the query.

**No-fabrication invariants** carried from storage: every returned number keeps
its `source_evidence_id` and condition; `comparison_validity` is never
overridden by ranking; unindexed layers degrade to LIKE search, never crash.

## Import semantics (`kb add`)

Upsert + Identity Resolution + Change Detection + Atomic Replacement + Provenance.

Resolution ladder, in order:

1. same `paper_id` → upsert that paper
2. same `doi` → resolve to the existing paper (`REASSIGNED_ID` if the suggested id differed)
3. same `source_hash`, different paper → **`DUPLICATE_SOURCE`** (blocks; `--force` overrides)
4. same title only → `POSSIBLE_DUPLICATE` (note; proceeds)

Change detection (on update): `SOURCE_CHANGED` (hash differs, allowed) ·
`EXTRACTION_UPDATED` (same source, newer processor) · `CITATION_KEY_DEDUP`.

Every replacement is a single SQLite transaction — a mid-write failure rolls
back and leaves the previous rows untouched. `processing_jobs` is an
append-only audit log; the archive keeps only the canonical latest package
plus a `manifest.json` with provenance. Full versioned archives are deferred.

## Canonical package

`kb add` accepts JSON (preferred) or YAML, in the shape emitted by the
`paper_to_literature_kb` Skill plus a small header:

```json
{
  "package_spec_version": "1.0",
  "processor": {"name": "paper_to_literature_kb", "version": "0.1.0"},
  "source": {"path": "...", "hash": "sha256:...", "type": "pdf"},
  "paper": {"L0": {}, "L1": {}, "L2": {}, "L3": {}, "L4": {}},
  "formulas": [], "citation_records": [], "citation_graph": [],
  "validation_report": {}
}
```

With `--source`, the original document is copied into `raw/<paper_id>/source/`
and its hash cross-checked against the header (mismatch → `SOURCE_HASH_MISMATCH`).

## Tests

```bash
python -m pytest tests/ -q
```

## Status of the spec's 18 collections

| collection | v1 tooling |
|-----------|-----------|
| papers, paper_cards, paper_methods, paper_metrics, paper_comparisons, paper_claims, paper_evidence, paper_fulltext (pointer), formulas, formula_variables, citation_records, citation_graph | **implemented** (written/read by `kb add`) |
| processing_jobs, validation_reports | **implemented** (audit log on import) |
| concepts, metrics_ontology | schema + seed hook; no tooling yet |
| citation_styles | reserved |
| embeddings | reserved (vector index deferred) |
