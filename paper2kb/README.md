# paper2kb — Paper-to-Literature-KB skill

Implements `Paper_to_Literature_KB_Skill_v1.0.md`: converts a paper source
(PDF / markdown / XML / LaTeX / text) into the **canonical KB package** that
`kb add` ingests — L0–L4, formulas, citation metadata, citation graph, and a
validation report.

## Pipeline

```
source ──parse──> sections+pages ──metadata(Crossref/PDF/LLM)──> reconciled L0
        ──extract──> L0 | L1 | L2-method | L2-results | L3 | formulas | graph
        (7 per-layer LLM calls) ──normalize(no-fabrication)──> canonical package
        ──validate(kb gates + human-review)──> package.json
```

- **Per-layer LLM calls** (spec: structured over raw): each layer is one focused
  DeepSeek call; a single layer's failure aborts cleanly.
- **Metadata priority** (spec §18): Crossref (DOI/title) > PDF metadata >
  document title; DOIs are never guessed.
- **No fabrication** is enforced in the prompts AND the normalizers: an invalid
  metric status becomes `unclear`, a reported metric without a value becomes
  `not_reported`, an unsubstantiated `improves` becomes `cites`, an unknown
  variable meaning becomes `unclear`, and `source_text` stays verbatim.
- **Validation reuses the KB's own gates** (`kb.package.validate_package`) —
  no duplicated logic — plus the spec §18 human-review triggers.

## Usage

```bash
cd writing-agent
python -m paper2kb paper.pdf --out package.json --doi 10.1109/...
python -m paper2kb paper.md  --out package.json              # Crossref by title
python -m paper2kb paper.tex --title "KAN-based Mask Optimization" --out pkg.json

# pipe straight into the KB
python -m paper2kb paper.pdf --out /tmp/pkg.json \
  && cd literature_kb && python -m kb add /tmp/pkg.json --source ../paper.pdf
```

Requires `DEEPSEEK_API_KEY` (same convention as `write_agent`). Without a key,
`python -m paper2kb` prints a clear error; the pipeline itself is fully
testable offline with an injected mock LLM.

## Tests

```bash
python -m pytest paper2kb/tests/ -q
```

23 tests: parser (incl. real PyMuPDF PDF page extraction), Crossref mapping,
per-layer extraction + no-fabrication normalizers, validation + human-review
triggers, and an end-to-end tracer bullet that runs source → package →
`kb.add` ingest → search.

## Dependencies

`requests`, `PyYAML`, `PyMuPDF` (PDF text), `pytest`. Depends on sibling
packages imported from `writing-agent/`: `write_agent.llm` (DeepSeek client)
and `literature_kb.kb` (canonical validation; bootstrap in `paper2kb/_kb.py`).
