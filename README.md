# Writing Agent

An autonomous academic paper writing agent. Give it a research topic or a
narrative report, and it produces a structured LaTeX paper — with a built-in
review loop that iteratively improves the draft until it passes an
adversarial self-review.

The repository bundles two cooperating components:

- **Writing Agent** (`write_agent/`) — a self-contained Python pipeline that
  plans, writes, reviews, and finalizes an academic paper using a single
  model (DeepSeek) with an internal adversarial review loop.
- **Literature Knowledge Base** (`literature_kb/`) — a hierarchical,
  SQLite-backed knowledge base that stores ingested papers and resolves
  citation hints so the writing agent never fabricates bibliography entries.

## Pipeline

```
Input (topic / narrative report)
        │
        ▼
 Phase 1  PLANNING     parse input → claims-evidence matrix → PAPER_PLAN.md
        │
        ▼
 Phase 2  WRITING      section-by-section LaTeX (abstract → intro → related
        │              → method → experiments → conclusion)
        ▼
 Phase 3  REVIEW LOOP  review → fix → re-review, up to N rounds, until
        │              score ≥ 6/10 AND verdict ∈ {ready, almost}
        ▼
 Phase 4  FINALIZE     5-pass writing audit + consistency checks → report
```

## Design principles

- **Claims-Evidence Matrix** — every claim maps to evidence; every experiment
  supports a claim. Claims without evidence are marked `needs_experiment`,
  never fabricated.
- **Seven-sentence abstract** — field context → specific background → gap →
  method part 1 → method part 2 → quantitative results → significance
  (embedded in `write_agent/prompts.py`).
- **Zero-context reviewer independence** — each review round sees ONLY the
  current paper text, never "what we changed last round". Prior-round
  summaries inflate scores, so the agent never leaks them. The only evidence
  of improvement is the current draft.
- **Adversarial stance** — the reviewer starts from "this work is broken
  somewhere; find where."
- **No hallucinated citations** — every `\cite{}` key resolves to a real
  bibliography entry verified **KB-first, then DBLP → CrossRef**.
  Unverifiable citations are dropped, never invented.
- **Crash recovery** — `REVIEW_STATE.json` + `PAPER_REVIEW_LOG.md` let a
  killed run resume from the last round.
- **Formatting & citation hygiene** — the 8 mechanical rules (hyperref
  `hidelinks`, 7-sentence abstract, 4-part figure discussions, no
  `\textbf{Summary:}` labels, `0.75\columnwidth` figures with `[!htb]`,
  `\cite{}` ≤ 3 keys each, exactly 30 references, no orphan bib entries) are
  **embedded directly in the writer / reviewer / fixer / final-audit prompts**
  in `write_agent/prompts.py` — they augment the full methodology above, they
  do not replace it.

## Literature Knowledge Base

Self-contained storage + retrieval layer for the hierarchical literature KB
(see `Literature_Knowledge_Base_RAG_Spec_v1.0.md`). Every ingested source is
copied into the KB; it is the writing agent's sole data source and is fully
decoupled from any external paper vault (e.g. `ILT project/raw/`).

### Layout

```
literature_kb/
├── kb/                    # Python package — the tools
│   ├── schema.py          # SQLite DDL (all 18 collections + sequences)
│   ├── store.py           # KBStore: counters, reads, atomic write, archive
│   ├── package.py         # canonical package: load / validate / normalize
│   ├── importtool.py      # kb add: identity ladder + change detection
│   ├── ids.py             # paper_id / citation_key / sub-id generators
│   ├── retrieve.py        # RetrievalService facade (resolve_hint, retrieve)
│   ├── router.py          # intent → layer → search strategy routing
│   └── cli.py             # kb init | add | list | status | search | ...
├── data/                  # KB_ROOT (default; override with $KB_ROOT or --root)
│   ├── kb.db              # SQLite (WAL, FK on)
│   ├── raw/<paper_id>/    #   package.json + manifest.json + source/
│   └── vectors/           # reserved for the embedding index
└── tests/
```

### Identity model (frozen)

| field | role | example |
|-------|------|---------|
| `paper_id` | human-readable internal identity | `ILT_2024_031` |
| `doi` | external canonical identity (unique) | `10.1016/...` |
| `source_hash` | integrity / change detection | `sha256:...` |
| `citation_key` | writing-agent internal reference | `Zhang2024DeepLearning` |
| `bibtex_key` | BibTeX artifact key (separate!) | `zhang2024deepilt` |

These five are **distinct and never merged**.

### Usage

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

`KB_ROOT` precedence: `--root` flag > `$KB_ROOT` env var > `<literature_kb>/data`.

### Retrieval Router (read side)

`RetrievalService.retrieve(query, intent, filters?, max_tokens?)` is the single
facade the writing agent calls. It routes the intent to the right layer and
search strategy, ranks by hybrid relevance, truncates to the token budget, and
returns a structured `ResultSet` — never raw text.

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

### Import semantics (`kb add`)

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
plus a `manifest.json` with provenance.

### Canonical package

`kb add` accepts JSON (preferred) or YAML, in the shape emitted by the
`paper_to_literature_kb` skill (`paper2kb/`) plus a small header:

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

## Reference paper

A complete reference paper is included at `output/paper/` (SPIE format,
14 pages) so you can compare your output's quality and formatting against a
known-good result. Its LaTeX source (`main.tex` + `sections/`) demonstrates
every rule above in practice.

## Installation

```bash
cd writing-agent
pip install -r requirements.txt          # requests, PyYAML, python-dotenv
cp .env.example .env                      # then add your DeepSeek API key
```

Or export the key:

```bash
export DEEPSEEK_API_KEY=sk-...
```

## Usage

```bash
# From a research topic / brief
python -m write_agent.cli --topic "Robust federated learning under label noise"

# From a narrative report or experiment data file
python -m write_agent.cli --narrative NARRATIVE_REPORT.md

# Common options
python -m write_agent.cli --topic "..." --venue NeurIPS --max-rounds 4
python -m write_agent.cli --topic "..." --skip-review          # draft only
python -m write_agent.cli --topic "..." --human-checkpoint     # pause per round
python -m write_agent.cli --topic "..." --model deepseek-reasoner
```

### Experiment-loop integration

Use a structured Evidence Bundle to enable the two-way research loop. With
`--auto-experiments`, a missing-evidence request is validated and accepted by
`model-optimize-loop`, missing runs execute in the background, results are
registered and returned, and writing resumes automatically:

```bash
python -m write_agent.cli --narrative NARRATIVE_REPORT.md --experiment-bundle evidence_bundle.json \
  --auto-experiments --loop-root ../model-optimize-loop \
  --project-profile ../model-optimize-loop/projects/lpd_ilt.yaml \
  --workspace-root ../lithobench \
  --experiment-python D:/ANACONDA/envs/lithobench/python.exe
```

For another team method, copy the project profile and change its runner,
contract paths, and result layout. The legacy `--lithobench-root` flag remains
available when no profile is supplied.

Manual request conversion remains available as a recovery/debugging workflow.
See
[`docs/experiment-loop-integration.md`](docs/experiment-loop-integration.md)
for the complete workflow and safety boundary.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--topic` / `--narrative` | *(required)* | Input: inline topic or path to a report |
| `--venue` | `ICLR` | `ICLR` `NeurIPS` `ICML` `CVPR` `ACL` `AAAI` `IEEE_JOURNAL` `IEEE_CONF` `SPIE` |
| `--max-pages` | `9` | Main body page limit |
| `--max-rounds` | `3` | Review loop rounds |
| `--min-score` | `6.0` | Review stop threshold |
| `--skip-review` | off | Write a draft, skip the review loop |
| `--human-checkpoint` | off | Pause after each review round |
| `--model` | `deepseek-chat` | `deepseek-chat` or `deepseek-reasoner` |
| `--output-dir` | `./output` | Where `paper/` is created |
| `--experiment-bundle` | unset | Enable experiment-aware writing from schema v1.0 evidence |
| `--resume PAPER_DIR` | unset | Resume a paper waiting for experiment responses |
| `--auto-experiments` | off | Dispatch requests, run missing experiments, return evidence, and resume automatically |
| `--loop-root` | unset | Path to `model-optimize-loop` |
| `--project-profile` | unset | YAML/JSON contract for the member method; omitted means legacy LPD-ILT mode |
| `--workspace-root` | unset | Path to the member method's experiment repository |
| `--lithobench-root` | unset | Deprecated alias for `--workspace-root` |
| `--experiment-python` | current Python | Python executable containing the method's experiment environment |

> **SPIE note**: the included example paper (`output/paper/main.pdf`) uses the
> SPIE proceedings template (`spie.cls` + `spiebib.bst`). The built-in venue
> templates cover ICLR/NeurIPS/ICML/IEEE. For an SPIE-format paper, generate
> with a supported venue (e.g. `ICLR` or `IEEE_CONF`) then replace the
> `\documentclass` line and copy `spie.cls` / `spiebib.bst` from
> `output/paper/` into the generated directory — the example paper in
> `output/paper/main.tex` shows the exact SPIE preamble to use.

## Output

```
output/paper/
├── PAPER_PLAN.md            # outline + claims-evidence matrix + figure/citation plan
├── main.tex                 # master LaTeX file
├── math_commands.tex        # shared notation macros
├── sections/
│   ├── 0_abstract.tex
│   ├── 1_introduction.tex
│   ├── 2_related_work.tex
│   ├── 3_method.tex
│   ├── 4_experiments.tex
│   └── 5_conclusion.tex
├── references.bib           # only verified, cited entries
├── PAPER_REVIEW_LOG.md      # full per-round review history
├── REVIEW_STATE.json        # crash recovery state
└── PIPELINE_REPORT.{md,json} # final report
```

Compile with any LaTeX toolchain:

```bash
cd output/paper
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

## Citation verification

The agent never fabricates bibliography entries. Citation hints from the plan
are resolved **KB-first**, then fall back to online sources:

1. **Literature KB** — the hint is matched against stored papers (fuzzy title
   matching tolerates paraphrased subtitles); a hit returns the stored BibTeX
   verbatim, keyed by its internal BibTeX key.
2. **DBLP** — search by exact title, fetch the publisher's real `.bib`.
3. **CrossRef** — title search / DOI lookup, converted to BibTeX.
4. **Unverified** — if no source matches with high confidence, the citation is
   dropped from `.bib` and reported for manual follow-up.

## Configuration

See `config.yaml` — all pipeline constants (venue, page limits, round counts,
scores, endpoints) are configurable. Environment variables with the prefix
`WRITING_AGENT_` override YAML (e.g. `WRITING_AGENT_MODEL_NAME`,
`WRITING_AGENT_MAX_ROUNDS`). Point the writing agent at the KB with
`WRITING_AGENT_KB_PATH=literature_kb/data` (or `write.kb_path` in YAML).

## Tests

```bash
python -m pytest tests/ literature_kb/tests/ -q   # writing agent + KB suites
```

The suite runs the full pipeline against a mock LLM (no API key or network
needed) plus unit tests for the citation resolver, JSON parsing, and the KB
storage/retrieval layers.

## Limitations

- Single-model self-review is less adversarial than cross-model review (one
  model writes, another reviews). The zero-context reviewer independence guard
  mitigates score inflation but cannot fully replace a second model family.
- arXiv title variants (e.g. `BERT: Pre-training of ...`) may not resolve
  strictly when CrossRef's canonical title differs; the agent prefers
  correctness (drop) over approximation (fabricate).
- Figures are planned and described but not auto-generated (no code execution
  or data plotting) — supply figure files and insert them manually.

## License

MIT
