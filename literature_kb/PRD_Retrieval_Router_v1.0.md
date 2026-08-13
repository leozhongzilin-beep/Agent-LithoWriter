# PRD: Literature KB Retrieval Router + Retrieval Layers v1.0

- **Status**: `implemented` (2026-08-13 — 112 tests, ruff clean; no issue tracker configured, this file is canonical until pushed to `github.com/leozhongzilin-beep/Agent-LithoWriter`)
- **Milestone**: KB read-side (retrieval). Write-side (storage) landed in `literature_kb/` 2026-08-13 — 51 tests, ruff clean.
- **Spec source**: `writing-agent/Literature_Knowledge_Base_RAG_Spec_v1.0.md` (§13 Retrieval Architecture, §14 Hybrid Retrieval, §15 Return Contract, §16 Hallucination Control, §21 Agent API)
- **Target repo**: `https://github.com/leozhongzilin-beep/Agent-LithoWriter`

---

## Problem Statement

The Literature Knowledge Base can **store** papers (`kb add`) but cannot **answer** anything. A research writing agent facing a task like "cite evidence that deep learning reduces ILT runtime" has no way to get a token-cheap, traceable answer. If it searches at all, it must either scan raw files or read full text — burning context, mixing evidence layers, and returning numbers without source attribution.

Three concrete failures today:

1. **No read side** — everything written by `kb add` is inert; there is no query path.
2. **No layer discipline** — an agent cannot get "just the L0 index card" vs "the L2 metric with its condition" vs "the L3 evidence sentence with page number" as separate, budgeted answers.
3. **No traceability back** — nothing enforces that a quantitative claim returned to the agent carries `source_evidence_id` → paper → section → page, which the spec (§16) requires to prevent fabrication.

## Solution

Build the **read side** of the KB: a `RetrievalRouter` that translates a writing-agent *intent* (not a raw "search full text" command) into a **plan** — which layers to hit, in what order, with what token budget and search strategy — plus **per-layer searchers** (L0–L3, formulas, citation) and a **structured return contract** that is traceable and budget-truncated.

The agent calls one facade:

```text
retrieve(query, intent, filters?, max_tokens?) -> ResultSet
```

and receives already-ranked, already-budgeted, already-traceable results with a `next_action` hint — never raw text, never full text.

Retrieval is **progressive** (L0 → L1 → L2 → L3, escalating only when the current layer is insufficient) and **hybrid** (metadata filter + FTS5/BM25 + citation-graph centrality + concept expansion when ontology exists). All search is over the KB's own schema — zero external services.

## User Stories

**Research writing agent (the primary actor):**

1. As a research writing agent, I want to send an intent (`DISCOVERY | CITATION | TECHNICAL | RESULT | FORMULA | VERIFICATION | COMPARISON`) instead of a raw text query, so that the system chooses the right layer and search strategy for my task.
2. As a research writing agent, I want a `DISCOVERY` query to return only L0 index cards (title, one-liner, citation_key, rendered citation, relevance), so that I can scan candidate papers for ~50–150 tokens each.
3. As a research writing agent, I want a `CITATION` query to resolve a paper to its `citation_key` and a rendered in-text/bibliography citation, so that I can write citations into a draft without touching BibTeX.
4. As a research writing agent, I want a `TECHNICAL` query to return the L1 paper card (research problem, gap, method summary, recommended use tags), so that I understand a paper without reading it.
5. As a research writing agent, I want a `RESULT` query to return L2 metrics **with their experimental conditions** (dataset, pitch, wavelength, NA), so that I never quote EPE=2.1nm without its condition.
6. As a research writing agent, I want a `VERIFICATION` query to check whether a sentence like "the method reduces TAT by 90%" is supported, so that I never write unsupported quantitative claims.
7. As a research writing agent, I want every quantitative result I receive to carry `source_evidence_id` and evidence trace (paper → section → page), so that claims in my manuscript are auditable.
8. As a research writing agent, I want results truncated to my `max_tokens` budget with a `truncated: true` flag, so that I control context cost explicitly.
9. As a research writing agent, I want `next_action` hints (e.g. "escalate to L3 evidence"), so that I know exactly what to ask next.
10. As a research writing agent, I want different-condition metrics to be labeled `not_comparable` / `partially_comparable` rather than silently ranked, so that I don't fabricate cross-paper comparisons.

**Literature review agent:**

11. As a literature review agent, I want to filter discovery by year/domain/method (e.g. `year_from=2020, method=KAN`), so that related-work scans are scoped.
12. As a literature review agent, I want citation-graph-aware relevance (papers more cited by KB papers rank higher), so that seminal work surfaces in reviews.
13. As a literature review agent, I want concept expansion to surface alias-equivalent papers (e.g. "AI-ILT" ↔ "learning-based mask optimization") when the ontology is seeded, so that queries are recall-complete.
14. As a literature review agent, I want a `FORMULA` search to return `formula_latex` + role + variable meanings + source evidence, so that a methods survey can compare objective/loss formulations.

**Experiment planning agent:**

15. As an experiment planning agent, I want `RESULT` queries to surface baselines/ablations/proposed separately, so that I can design a fair comparison set.
16. As an experiment planning agent, I want `verify_claim` to return claim strength (A/B/C/D) and its supporting evidence, so that I trust the grounding of a prior result before basing an experiment on it.

**Citation manager:**

17. As a citation manager, I want `resolve_citation(paper_id, style_id)` to return a rendered citation from the `citation_records` cache, so that the writing agent never re-renders by hand.
18. As a citation manager, I want a minimal `(Author, Year)` fallback when no cached rendering exists — explicitly marked `generated: true` — so that full CSL rendering remains a separate concern.

**Researcher / KB operator:**

19. As a researcher, I want a `kb search` CLI so that I can exercise the retrieval layer interactively against a real KB.
20. As a researcher, I want search to degrade gracefully (FTS5 missing → LIKE fallback; empty ontology → no concept expansion), so that retrieval never crashes on environment gaps.
21. As a researcher, I want newly imported papers (via `kb add`) to be immediately searchable, so that the write and read sides never drift.
22. As a researcher, I want `retrieve()` to be pure-ish and testable, so that ranking and budget behavior are deterministic and unit-testable.

## Implementation Decisions

### Modules (new, inside `literature_kb/kb/`)

| Module | Depth | Responsibility |
|--------|-------|----------------|
| `router.py` | deep, pure | Intent → `RoutePlan` decision table (§13.1, §13.3) |
| `contract.py` | deep, pure | Return-contract dataclasses + token estimation + budget truncation (§15) |
| `relevance.py` | deep, pure | Hybrid scoring/rerank to 0–1 (BM25 + recency + metadata + graph) |
| `fts.py` | deep, DB | FTS5 virtual-table lifecycle: create / per-paper sync / `bm25` query |
| `search.py` | DB | L0 discovery / L1 card / L2 metrics + comparisons searchers |
| `evidence.py` | DB | L3 evidence search + `verify_claim` (A/B/C/D strength mapping) |
| `formula.py` | DB | Formula search (role filter + keyword over latex/semantic/variables) |
| `citation.py` | DB, light | `resolve_citation` (cache-first, minimal fallback) |
| `retrieve.py` | orchestrator | `RetrievalService.retrieve()` — runs the progressive L0→L4 loop |

Existing modules touched: `schema.py` (add FTS5 virtual tables; re-running `kb init` is idempotent and safe), `importtool.py` + `store.py` (after a paper's rows are committed, call the FTS per-paper sync in the same transaction so new papers are instantly searchable), `cli.py` (add `search` / `get-card` / `metrics` / `verify` / `formula` / `cite`).

### Intent → RoutePlan (decision-rich; encode as a table)

```text
Intent        Start     Escalation      Default budget  Strategy stack
DISCOVERY     L0        L1 (on demand)  L0 rows ≤ ~150 tok/paper
CITATION      L0        —               ≤ 400 tok        citation_key/doi lookup → citation_records
TECHNICAL     L1        L2              ≤ 1500 tok       keyword + BM25 over card fields
RESULT        L2        L3 (evidence)   ≤ 1500 tok       metric-name filter + condition filter
FORMULA       Formula   —               ≤ 800 tok        role filter + keyword over latex/semantic/variables
VERIFICATION  L3        —               ≤ 1500 tok       evidence BM25 + claim match + strength mapping
COMPARISON    L2        L3              ≤ 1200 tok       comparisons + evidence cross-check
```

`RoutePlan = {mode, layers: [..], budget, strategy: [..]}`; budgets are overridable by `max_tokens`. Escalation is progressive: a layer runs only if the previous layer returned and the caller still needs more (satisfied by `next_action` hints; the facade auto-escalates while budget remains and the next layer is non-empty).

### Return contract (decision-rich type shape)

```text
ResultSet { query, mode, results: [ResultItem], next_action, truncated }
ResultItem { paper_id, title, relevance, why_relevant, best_use,
             key_fact, citation_key, citation, available_levels, evidence_ids }
EvidenceHit { evidence_id, paper_id, section, page, source_text, claim, confidence }
VerifyResult { claim, verdict: supported|unsupported|unverified,
               strength: A|B|C|D|None, evidence: [EvidenceHit], notes }
FormulaHit { formula_id, paper_id, formula_latex, formula_role,
             semantic_description, variables, source_evidence_id }
```

Searchers return these, never raw text. `estimate_tokens = max(1, len(text)//4)`; truncation drops lowest-relevance items until the running sum fits the budget, sets `truncated`.

### Relevance scoring (`relevance.py`, deterministic)

```text
score = 0.55 * bm25_norm + 0.20 * recency + 0.15 * metadata_match + 0.10 * graph_centrality
bm25_norm        min-max normalized FTS5 bm25() over the candidate set
recency          (year - 2000) / 30 clipped to [0,1]
metadata_match   fraction of supplied filters (domain/method/year/venue) satisfied
graph_centrality normalized in-degree in citation_graph (seminality)
```

Concept expansion: when `concepts.aliases` contains a query token, append alias tokens to the query before BM25; no-op when the ontology table is empty. This is a hook, not a dependency.

### FTS5 (`fts.py`)

- Confirmed available in this environment's stdlib `sqlite3` (`CREATE VIRTUAL TABLE ... USING fts5` + `bm25()`). Zero new dependencies.
- Three contentless virtual tables: `fts_papers` (title, keywords, domain_tags, method_tags, one_line_description), `fts_evidence` (source_text, claim, section), `fts_formulas` (formula_latex, semantic_description, variables).
- Sync strategy: on `kb add`, after `write_package` commits, delete that paper's FTS rows and re-insert from the just-written rows — same transaction, so searchability and write atomicity stay aligned.
- Query path: escape user tokens, `... MATCH ?`, order by `bm25()`; malformed FTS query syntax degrades to a LIKE fallback. FTS5 absence degrades to LIKE fallback (behavior preserved, ranking weaker).

### `resolve_citation` behavior

1. `citation_records WHERE paper_id=? AND style_id=?` → return `in_text_citation` + `bibliography_entry` (`generated: false`).
2. Else build minimal in-text `(FirstAuthor, Year)` from `bibliographic_record`, mark `generated: true`.
3. Full CSL rendering engine is **out of scope**; `citation_styles` table remains reserved.

### No-fabrication invariants (carried from the storage milestone)

- Every returned metric/claim carries its `source_evidence_id`; nothing numeric is synthesized.
- `verify_claim` never upgrades evidence: no evidence → `unverified`; evidence present → strength mapped from the claim's stored `strength` (A/B/C/D) and evidence confidence, never invented.
- `comparison_validity` is surfaced (`comparable` / `partially_comparable` / `not_comparable`) and never overridden by ranking.
- Unindexed/empty layers return empty results with an explicit `next_action`, not an error and not fabricated rows.

## Testing Decisions

- **What a good test is**: external behavior only — given a populated KB (via the existing `make_package` factory + `kb.add`), a `(query, intent, filters, budget)` must produce the correct mode/layers, ranked results, traceable evidence, correct budget truncation, and honest `unverified` verdicts. No assertions on private internals.
- **Modules tested (all)**:
  - Pure/deep: `router` (intent→plan table, budgets), `contract` (token estimation, truncation), `relevance` (score composition, normalization, concept-expansion no-op), `fts` (create/sync/query/fallback).
  - DB-backed: `search` (L0/L1/L2 + filters), `evidence` (search + `verify_claim` strengths), `formula` (role filter), `citation` (cache + fallback), `retrieve` (end-to-end progressive escalation).
- **Prior art**: tests already use the `tmp_kb` fixture + `make_package` factory (`tests/conftest.py`); `test_import.py` covers the write side with the same seam. Retrieval tests mirror that: seed a KB with 2–3 synthetic packages, assert on the returned contract. Pure modules are tested without a DB.

## Out of Scope

- **Vector/embedding retrieval** — `embeddings` table + `vectors/` stay reserved; a follow-on PRD introduces an embedding model.
- **L4 paragraph chunking** — full-text parsing → section/paragraph chunks; `paper_fulltext.chunks` stays reserved.
- **Ontology seeding content** — `concepts` / `metrics_ontology` schema + expansion hook only; curated content is a separate data task.
- **Full CSL citation rendering engine** — `resolve_citation` returns cache or minimal fallback only.
- **Writing-agent pipeline integration** — wiring `retrieve()` into `writing-agent` prompts is a separate PRD.
- **The `paper_to_literature_kb` Skill implementation** — this PRD only consumes its output package shape.

## Further Notes

- **Environment facts verified 2026-08-13**: FTS5 available in this Python's stdlib sqlite3; no `gh` CLI / no git repo in the workspace — the PRD is a file (`literature_kb/PRD_Retrieval_Router_v1.0.md`) until the `Agent-LithoWriter` repo is set up; pushing implementation there is expected after this milestone.
- **Frozen design carried forward**: five-way identity separation (paper_id / doi / source_hash / citation_key / bibtex_key); KB fully self-contained; `processing_jobs` = audit log. The router must not weaken any of these.
- **Token budgets** reference spec §13.3; `DISCOVERY` must honor the ~50–150 tokens/paper shape by returning only L0 fields.
- **`kb init` idempotency**: adding FTS tables means an existing KB only needs `kb init` re-run; `schema.create_schema` already tolerates existing tables.
