# Writing Agent

An autonomous academic paper writing agent. Give it a research topic or a
narrative report, and it produces a structured LaTeX paper — with a built-in
review loop that iteratively improves the draft until it passes an
adversarial self-review.

**Inspired by [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep)**
(Auto Research In Sleep). ARIS orchestrates research via composable skills
with cross-model adversarial review; this agent distills that design into a
self-contained Python program using a **single model (DeepSeek) with an
internal review loop** — no Codex MCP, no multi-model coordination required.

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

## Design principles (borrowed from ARIS)

- **Claims-Evidence Matrix** — every claim maps to evidence; every experiment
  supports a claim. Claims without evidence are marked `needs_experiment`,
  never fabricated.
- **Seven-sentence abstract** — field context → specific background → gap →
  method part 1 → method part 2 → quantitative results → significance
  (embedded in `write_agent/prompts.py`).
- **Zero-context reviewer independence** — each review round sees ONLY the
  current paper text, never "what we changed last round". This is ARIS's
  `REVIEWER_BIAS_GUARD`: prior-round summaries inflate scores, so the agent
  never leaks them. The only evidence of improvement is the current draft.
- **Adversarial stance** — the reviewer starts from "this work is broken
  somewhere; find where."
- **No hallucinated citations** — every `\cite{}` key resolves to a real
  bibliography entry verified via **DBLP → CrossRef**. Unverifiable citations
  are dropped, never invented.
- **Crash recovery** — `REVIEW_STATE.json` + `PAPER_REVIEW_LOG.md` let a
  killed run resume from the last round.
- **Formatting & citation hygiene** — the 8 mechanical rules (hyperref
  `hidelinks`, 7-sentence abstract, 4-part figure discussions, no
  `\textbf{Summary:}` labels, `0.75\columnwidth` figures with `[!htb]`,
  `\cite{}` ≤ 3 keys each, exactly 30 references, no orphan bib entries) are
  **embedded directly in the writer / reviewer / fixer / final-audit prompts**
  in `write_agent/prompts.py` — they augment the full methodology above, they
  do not replace it.

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
are resolved through:

1. **DBLP** — search by exact title, fetch the publisher's real `.bib`.
2. **CrossRef** — title search / DOI lookup, converted to BibTeX.
3. **Unverified** — if neither source matches with high confidence, the
   citation is dropped from `.bib` (the reference may still appear as
   `\citep{key}` in the text; the final report lists missing entries for
   manual follow-up).

## Configuration

See `config.yaml` — all pipeline constants (venue, page limits, round counts,
scores, endpoints) are configurable. Environment variables with the prefix
`WRITING_AGENT_` override YAML (e.g. `WRITING_AGENT_MODEL_NAME`,
`WRITING_AGENT_MAX_ROUNDS`).

## Tests

```bash
python tests/test_smoke.py
```

The smoke suite runs the full pipeline against a mock LLM (no API key or
network needed) plus unit tests for the citation resolver and JSON parsing.

## Limitations

- Single-model self-review (DeepSeek) is less adversarial than ARIS's
  cross-model review (Claude writes, GPT reviews). The zero-context reviewer
  independence guard mitigates score inflation but cannot fully replace a
  second model family.
- arXiv title variants (e.g. `BERT: Pre-training of ...`) may not resolve
  strictly when CrossRef's canonical title differs; the agent prefers
  correctness (drop) over approximation (fabricate).
- Figures are planned and described but not auto-generated (no code execution
  or data plotting) — supply figure files and insert them manually.

## License

MIT
