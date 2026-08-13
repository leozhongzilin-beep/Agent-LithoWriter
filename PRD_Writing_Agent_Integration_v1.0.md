# PRD: Writing-Agent × Literature-KB Integration v1.0

- **Status**: `ready-for-agent` (2026-08-13 — design approved in brainstorming; no issue tracker configured, this file is canonical until pushed to `github.com/leozhongzilin-beep/Agent-LithoWriter`)
- **Milestone**: first `write_agent` ↔ `literature_kb` integration (reference resolution + related-work grounding)
- **Spec source**: `writing-agent/Literature_Knowledge_Base_RAG_Spec_v1.0.md` (§21 Agent API, §22 Writing Agent Integration Policy) and `writing-agent/literature_kb/PRD_Retrieval_Router_v1.0.md`
- **Sibling PRDs**: `literature_kb/PRD_Retrieval_Router_v1.0.md` (read side, implemented), `literature_kb/PRD_KB_Completion_v1.0.md` (completion, implemented)
- **Target repo**: `https://github.com/leozhongzilin-beep/Agent-LithoWriter`
- **Triage label**: `ready-for-agent` (applied in-file; no issue tracker)

---

## Problem Statement

The `write_agent` (DeepSeek academic writing agent) resolves citation hints **exclusively online**: every hint goes to DBLP, then CrossRef, then `UNVERIFIED`. The `literature_kb` holds the researcher's **curated, verified library** — canonical `bibliographic_record`, full BibTeX in `citation_cache.bibtex`, per-style rendered citations, L0–L3 evidence — but the writing agent never consults it. Three concrete failures:

1. **The curated library is ignored.** Papers already imported and verified in the KB are re-fetched (or missed) via network lookups, so `references.bib` can carry entries that diverge from the researcher's canonical records, or fail entirely offline.
2. **No provenance link.** A reference resolved via DBLP/CrossRef carries no `paper_id`, so it cannot be traced back to KB evidence (L2 metrics, L3 evidence sentences) that the researcher may want in the manuscript.
3. **Related work is not grounded.** The LLM writes related-work prose from parametric memory of "papers that sound related." It never sees the researcher's actual library, so it cannot synthesize the categories the KB actually contains, and it risks citing papers the user never curated.

The KB's own spec (§22) already prescribes the integration policy ("citation search starts at L0; citation keys used in body; quantitative claims carry evidence trace"); this PRD makes the reference-resolution and related-work portions of that policy concrete.

## Solution

Two seams, KB-first and config-gated:

**A. Reference resolution — KB first.** The existing pre-resolution loop in `run_write` (collect hints → resolve → build `citation_keys` + `resolved_entries` → `write_bibliography`) gains a KB-first phase. Each hint is first resolved against the KB via a new `RetrievalService.resolve_hint()` facade: on hit, the `VerifiedEntry` is built from the KB's canonical record (source=`KB`, bibtex verbatim from `citation_cache.bibtex`, draft `\cite` key = the stored BibTeX's own internal key). On miss — or on a hit whose stored BibTeX is absent — resolution falls through to the existing DBLP → CrossRef → `UNVERIFIED` ladder unchanged.

**B. Related-work grounding — KB cards injected.** When the related-work section is written, the KB's `DISCOVERY` retrieval is queried per category topic; the top-N (default 5) cards are **auto-enqueued as citation hints**, resolved through the KB-first resolver (they are in the KB, so they resolve instantly), and injected into the related-work prompt as a `KB KNOWN WORK AVAILABLE` block. The LLM can then cite papers that actually exist in the researcher's library, while the no-orphan / no-undefined-citation invariants are preserved because only resolved-and-citable cards are injected and only `CITATION KEYS AVAILABLE` keys may be cited.

The KB is **fully optional**: no `kb_path` configured → the provider is `None` → today's behavior, byte for byte.

## User Stories

**Research writing agent (primary actor):**

1. As a research writing agent, I want citation hints resolved against the KB first, so that references come from my curated, verified library rather than random online lookups.
2. As a research writing agent, I want a KB hit to yield the full BibTeX verbatim from the KB, so that `references.bib` entries are byte-identical to the imported record.
3. As a research writing agent, I want a KB-resolved reference to carry `source=KB` and its `paper_id`, so that provenance is visible and traceable back to KB evidence.
4. As a research writing agent, I want the draft's `\cite` key to be the stored BibTeX's own internal key (no rewriting), so that body citations and `references.bib` always match.
5. As a research writing agent, I want a fast path when the hint is already a KB `citation_key` (e.g. `Zhang2024DeepILT`), so that reusing a known paper resolves without title ambiguity.
6. As a research writing agent, I want KB candidates gated by the same strict title-match heuristic used for DBLP/CrossRef, so that a fuzzy hint never pulls the wrong paper.
7. As a research writing agent, I want KB hits with no stored BibTeX to fall back to online resolution, so that `references.bib` is always compilable.
8. As a research writing agent, I want the DBLP → CrossRef → `UNVERIFIED` ladder preserved when the KB misses, so that the integration never reduces citation coverage.
9. As a research writing agent, I want a KB-only mode (no network) when `dblp_verify` is off but a KB is configured, so that fully offline writing against the curated library works.
10. As a research writing agent, I want related-work writing grounded in KB discovery cards (title / one-line / year / citation key) per category, so that the section synthesizes papers that actually exist in my library.
11. As a research writing agent, I want discovery-surfaced papers automatically enqueued as resolvable citations, so that I can cite grounded work rather than only describe it.
12. As a research writing agent, I want only citable cards (resolved and carrying BibTeX) injected into the prompt, so that the no-orphan / no-undefined-citation invariants hold.
13. As a research writing agent, I want a per-category card cap (default 5), so that token cost stays bounded.
14. As a research writing agent, I want the 30-unique-refs / no-orphan / ≤3-keys-per-`\cite` invariants preserved, so that the compiled paper stays clean.
15. As a research writing agent, I want the KB provider to be absent (or import-failure-safe) when `literature_kb` is not installed, so that the standalone agent runs unchanged.

**Researcher / KB operator:**

16. As a researcher, I want `resolve_hint` exposed as a first-class `RetrievalService` method, so that the KB owns the citation contract and any agent can reuse it.
17. As a researcher, I want `resolve_hint` to return top-N (≤3) deduplicated candidates with BibTeX extracted from the canonical cache, so that ambiguous hints get a ranked, reviewable choice.
18. As a researcher, I want empty or uninitialized KBs to return an empty list rather than erroring, so that agents degrade gracefully.
19. As a researcher, I want the related-work grounding to reuse the existing `DISCOVERY` retrieval (via `retrieve(query, intent="DISCOVERY")`) with no new KB search machinery, so that only one new KB method is introduced.
20. As a researcher, I want the write agent to depend on a narrow injectable provider protocol, so that unit tests run without a database and the coupling stays one-directional.

**KB maintainer:**

21. As a KB maintainer, I want **no schema changes** and no new dependencies, so that existing KBs keep working without re-running `kb init`.
22. As a KB maintainer, I want `resolve_hint` to prefer the exact `citation_key` match first, then DOI, then title search, so that identity resolution is deterministic and cheap.

**Citation manager:**

23. As a citation manager, I want KB in-text citations for cards produced by the existing `resolve_citation`, so that agents never hand-format citations.
24. As a citation manager, I want the final `.bib` to remain a plain BibTeX file (CSL venue rendering stays the KB's `kb bibliography` job), so that responsibilities don't blur.

## Implementation Decisions

### Module inventory

**Modified — `literature_kb` (`kb.retrieve` module):** one new method + one new frozen dataclass. No schema change, no new dependencies.

**Modified — `write_agent` (4 files):** `citation.py` (KB-first resolve path), new `kb_bridge` adapter module (optional import + provider protocol), `config.py` (two new keys), `phases/write.py` (related-work grounding flow). `prompts.py` gains one placeholder.

### Contract — KB side

```text
@dataclass(frozen=True)
ResolvedCitation:
    paper_id: str
    citation_key: str      # CamelCase KB logical id, e.g. Zhang2024DeepILT
    bibtex: str            # verbatim @article{...} from citation_cache.bibtex; "" if absent
    title: str
    year: str
    venue: str
    in_text: str           # rendered in-text citation (existing resolve_citation, minimal fallback ok)

RetrievalService.resolve_hint(hint: str) -> list[ResolvedCitation]
    # top-3, deduplicated by paper_id; [] on miss
```

`resolve_hint` logic, in order:
1. `store.find_by_citation_key(hint)` → exact-key hit, single candidate.
2. `store.find_by_doi(hint)` and `store.paper_exists(hint)` → candidate.
3. `search.search_l0(store, hint, limit=1)` → top candidate.
4. For each candidate: `bibtex = (paper["citation_cache"] or {}).get("bibtex", "")`, metadata from `bibliographic_record` + L0 (`title`, `year`, `venue`), `in_text` via `kb.citation.resolve_citation(store, pid)` (no style → minimal `(Author, Year)`; fine for cards).
5. Deduplicate by `paper_id`, cap at 3, return.

Empty / uninitialized KB → `[]`, never an exception. This is the **only** KB-side addition; discovery cards reuse the existing `retrieve(query, "DISCOVERY", max_tokens=...)` ResultItems.

### Contract — write_agent side

A narrow provider protocol so `write_agent` never imports `literature_kb` classes at module scope (keeps the standalone agent runnable and unit tests DB-free):

```text
class KbProvider(Protocol):
    def resolve_hint(self, hint: str) -> list[KbResolved]: ...      # wraps KB resolve_hint
    def discover_cards(self, topic: str, *, max_tokens: int, limit: int) -> list[KbCard]: ...
    # wraps existing retrieve(query, "DISCOVERY"); enriches each ResultItem with year via store.get_paper

KbResolved:  { citation_key, bibtex, title, year, venue, in_text }   # bibtex may be ""
KbCard:      { citation_key, title, one_line, year, in_text }        # informational; not necessarily citable yet
```

A `build_kb_provider(config) -> KbProvider | None` factory: lazily imports `literature_kb`, constructs `KBStore(config.kb_path)` + `RetrievalService`, returns an adapter; returns `None` when `kb_path` is unset or the import fails (graceful, today's behavior).

### Config

- `write.kb_path` — path to `kb.db` (e.g. `literature_kb/data/kb.db`). Unset / empty → provider `None`.
- `write.kb_discovery_per_category` — int, default `5`.
- Env override `WRITING_AGENT_KB_PATH` added to the config env map.

### Degradation model (mode matrix)

| `dblp_verify` | `kb_path` | Behavior |
|---|---|---|
| off | unset | `CitationResolver` not built (today's default-off) |
| on | unset | online-only (today's behavior) |
| off | set | **KB-only** — KB hit resolves; miss → `UNVERIFIED` (no network) |
| on | set | **KB-first, online fallback** (the main mode) |

`CitationResolver` is constructed when `dblp_verify or kb_path`; its KB-first phase runs only when a provider is present; the DBLP/CrossRef ladder runs only when `dblp_verify` is on.

### Reference-resolution flow (per hint)

1. If provider present: `resolve_hint(hint)`.
2. Accept the first candidate where **either** the candidate's `citation_key == hint` (exact-key fast path, no title gate) **or** `_title_matches(hint, candidate.title)` (reuse the existing heuristic, kept only in `write_agent`).
3. On accept: require `candidate.bibtex` non-empty; parse its first internal key via the existing `extract_cited_keys`; build `VerifiedEntry(key=<internal key>, bibtex=<verbatim>, title/year/venue=<KB values>, source="KB", verified=True)`.
4. If no acceptable candidate with BibTeX: fall through to DBLP → CrossRef → `UNVERIFIED` exactly as today.

### Related-work grounding flow (write phase)

1. When the related-work section is reached, derive category topics from the section's `key_points` / `citations_hint` / `citation_plan.related`.
2. Per topic: `provider.discover_cards(topic, max_tokens≈800, limit=kb_discovery_per_category)`.
3. Deduplicate card titles against already-seen hints; **enqueue new titles into the hint queue** and resolve them through `CitationResolver` (KB hits → instant), growing both `citation_keys` and `resolved_entries` in place **before** the section writer runs.
4. Keep only **citable** cards (resolved, BibTeX present) and format them into the `KB KNOWN WORK AVAILABLE` block.
5. Pass the block to the section writer; the prompt instructs: only `\cite` keys from `CITATION KEYS AVAILABLE`; a card whose key is absent may be described but not cited.
6. `write_bibliography` at the end consumes the grown `resolved_entries` — ordering invariant holds because discovery mutates the two lists before the related-work writer and they are only read at the end.

### Invariants preserved (frozen)

- `\cite{key}` in body == `@article{key,...}` in `references.bib` == `VerifiedEntry.key` (guaranteed by using the verbatim BibTeX internal key).
- Exactly 30 unique references, no orphans, ≤3 keys per `\cite` — enforced by the existing review loop; grounding must not weaken it.
- No fabricated BibTeX: KB-origin entries come from curated imports only; `verified=True` is set solely by KB presence of a non-empty `bibtex`.
- KB fully optional; `literature_kb` an optional import at runtime.

## Testing Decisions

- **What a good test is**: external behavior only — given a populated KB (via the existing `make_package` factory + `kb add`) or a fake provider, a `(hint → VerifiedEntry)` or `(topic → cards + prompt block)` must produce the correct source/key/bibtex, the correct fall-through, and the correct prompt content. No assertions on private internals.
- **Modules tested (all)**:
  - `literature_kb.kb.retrieve` — `resolve_hint`: exact `citation_key` hit; DOI hit; title-hit via L0; BibTeX extracted from `citation_cache.bibtex`; missing BibTeX → `""`; empty/uninitialized KB → `[]`; dedup + top-3 cap. **Prior art**: `tests/test_retrieve.py` (CITATION mode), `tests/conftest.py` (`make_package` + `tmp_kb` fixture).
  - `write_agent.citation` — with a fake `KbProvider` injected: KB hit → `VerifiedEntry(source="KB", key=<internal bibtex key>, bibtex verbatim)`; exact-key hint accepted without title gate; title mismatch → falls through to mocked DBLP/CrossRef; `bibtex=""` candidate → falls through; KB miss → unchanged ladder; cache behavior unchanged. **Prior art**: `tests/test_smoke.py` (`MockClient`, `VerifiedEntry`/`write_bibliography`/`make_key` unit tests).
  - `write_agent.kb_bridge` — `build_kb_provider` returns `None` when `kb_path` unset or import missing; adapter enriches discovery cards with year; `resolve_hint` passthrough.
  - `write_agent.phases.write` — with `MockClient` + fake provider: related-work prompt contains the `KB KNOWN WORK AVAILABLE` block; discovery-surfaced titles were enqueued and resolved (keys appear in `CITATION KEYS AVAILABLE`); non-citable cards are dropped; `kb_path` unset → no KB code path exercised.
  - `write_agent.config` — `kb_path` / `kb_discovery_per_category` / `WRITING_AGENT_KB_PATH` parse.
- **Regression gate**: a KB-first full-pipeline smoke run (like `test_full_pipeline` but with a seeded in-memory KB) still produces a compilable `references.bib` with exactly 30 unique entries and no orphan bib entries.
- **Run together**: `cd writing-agent && python -m pytest paper2kb/tests/ literature_kb/tests/ tests/ -q` and `python -m ruff check paper2kb/ literature_kb/ write_agent/` (the existing `test_cli.py` basename-collision note applies if new test files are named `test_cli*.py`).

## Out of Scope

- **Plan-phase grounding** — the planner consulting the KB to build `citation_plan` (changes the outline JSON schema; larger blast radius). Follow-up PRD.
- **L2 metrics / L3 evidence verification into experiment sections** — the rest of §22's policy (quantitative claims carrying evidence trace) stays a follow-up PRD.
- **CSL rendering inside the writing agent** — `references.bib` remains a plain `.bib`; venue-final rendering stays the KB's `kb bibliography` job.
- **Embedding / FAISS retrieval** — KB-side reserved (see Retrieval PRD).
- **Ontology content depth** (`kb/seeds/*.yaml`) — data task, not this PRD.
- **`kb init` / schema migration** — none needed; explicitly out of scope.

## Further Notes

- **Environment facts (2026-08-13)**: Python 3.13, FTS5 available in stdlib; `DEEPSEEK_API_KEY` not present in env — the integration must be testable entirely offline via fake providers + MockLLM (established paper2kb precedent). No new runtime dependencies for either package.
- **Design provenance**: decisions 2–8 of the brainstorming session are frozen here (scope = reference + related-work grounding; facade = KB-side `resolve_hint`; key = verbatim BibTeX internal key; title gate stays in `write_agent`; BibTeX-absent candidates fall through; cards auto-enqueued; cap default 5).
- **Frozen carry-forward**: five-way identity separation (paper_id / doi / source_hash / citation_key / bibtex_key) must not be weakened — `resolve_hint` treats `citation_key` as the identity for the fast path and never conflates it with the BibTeX internal key.
- **Suggested next skills**: `/tdd` (vertical slices, one RED→GREEN at a time), then `/orchestrate` or `writing-plans` for the multi-file implementation, then a real offline end-to-end run against a seeded KB before any online run.
