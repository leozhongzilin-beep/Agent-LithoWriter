# Writing-Agent × Literature-KB Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the writing agent resolve citation hints against the Literature KB first (verbatim BibTeX from `citation_cache.bibtex`) and ground related-work prose in KB discovery cards, with the KB fully optional.

**Architecture:** Two seams. (1) Reference resolution: `RetrievalService.resolve_hint()` (new, KB-side) returns top-N `ResolvedCitation`; `write_agent`'s `CitationResolver.resolve_query()` tries it before DBLP/CrossRef, gated by title-match and BibTeX presence, using the stored BibTeX's internal key as the draft `\cite` key. (2) Related-work grounding: `phases/write.py` queries KB `DISCOVERY` per category, auto-enqueues surfaced titles as hints, resolves them, and injects a `KB KNOWN WORK AVAILABLE` block into the related-work prompt. A `write_agent.kb_bridge` adapter makes `literature_kb` an optional, lazily-imported dependency; no `kb_path` config → identical behavior to today.

**Tech Stack:** Python 3.13, stdlib sqlite3 (FTS5), pytest, ruff. No new runtime dependencies.

**Spec:** [writing-agent/PRD_Writing_Agent_Integration_v1.0.md](../../../PRD_Writing_Agent_Integration_v1.0.md) — this plan argues from it; executors read both.

## Global Constraints

- **Run tests from `writing-agent/`**: `python -m pytest paper2kb/tests/ literature_kb/tests/ tests/ -q` — all three suites together (a `test_cli.py` basename collision exists; do not create new `test_cli*.py` files).
- **Ruff clean**: `python -m ruff check paper2kb/ literature_kb/ write_agent/`.
- **No schema changes** to `literature_kb` (no `kb init` migration), **no new dependencies**.
- **`literature_kb` stays an optional import** at `write_agent` runtime: `build_kb_provider` must return `None` (not raise) when `kb_path` is unset or the `kb` package is not importable.
- **BibTeX verbatim**: KB-origin `VerifiedEntry.bibtex` is the stored `citation_cache.bibtex` string, never rewritten; the draft cite key is the first internal key parsed from it via the existing `extract_cited_keys`.
- **Title gate**: a KB candidate is accepted only if `_title_matches(hint, candidate.title)` OR `candidate.citation_key == hint`; candidates with empty `bibtex` are rejected (online fallback).
- **Invariants preserved**: exactly 30 unique refs, no orphan bib entries, ≤3 keys per `\cite`, `\cite{key}` == `@article{key,...}` == `VerifiedEntry.key`.

---

### Task 1: KB-side `resolve_hint`

**Files:**
- Modify: `literature_kb/kb/retrieve.py` (append `ResolvedCitation` dataclass + `resolve_hint` method to `RetrievalService`)
- Test: `literature_kb/tests/test_resolve_hint.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ResolvedCitation:
      paper_id: str
      citation_key: str   # CamelCase KB id, e.g. Zhang2024DeepLearning
      bibtex: str         # verbatim citation_cache.bibtex; "" when absent
      title: str
      year: str
      venue: str
      in_text: str

  RetrievalService.resolve_hint(hint: str, limit: int = 3) -> list[ResolvedCitation]
  # top-N deduplicated by paper_id; [] on miss or empty KB; never raises
  ```
- Consumes (already in `literature_kb`): `self.store.find_by_citation_key(hint)`, `find_by_doi(hint)`, `paper_exists(hint)`, `get_paper(pid)` (returns parsed dicts incl. `citation_cache`), `search.search_l0(store, hint, limit=limit)` (returns ResultItems with `.paper_id`), `resolve_citation(store, pid)["in_text_citation"]` (already imported in `retrieve.py`).

- [ ] **Step 1: Write the failing test** — create `literature_kb/tests/test_resolve_hint.py`:

```python
"""Tests for RetrievalService.resolve_hint() (kb/retrieve.py)."""
from __future__ import annotations

from kb.importtool import import_package
from kb.retrieve import ResolvedCitation, RetrievalService


def _seed(tmp_kb, make_package, **L0_overrides):
    pkg = make_package()
    pkg["paper"]["L0"].update(L0_overrides)
    pkg["citation_records"] = []
    return import_package(tmp_kb, pkg).paper_id


def test_resolve_hint_by_citation_key(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("Zhang2024DeepLearning")
    assert len(hits) == 1
    assert hits[0].citation_key == "Zhang2024DeepLearning"
    assert hits[0].title == "Deep Learning for Inverse Lithography"
    assert "@article{zhang2024deepilt" in hits[0].bibtex
    assert "Zhang" in hits[0].in_text


def test_resolve_hint_by_doi(tmp_kb, make_package):
    pid = _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("10.1016/x1")
    assert len(hits) == 1 and hits[0].paper_id == pid


def test_resolve_hint_by_title_search(tmp_kb, make_package):
    _seed(tmp_kb, make_package)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("deep learning for inverse lithography")
    assert hits and "lithography" in hits[0].title.lower()


def test_resolve_hint_missing_bibtex_is_empty_string(tmp_kb, make_package):
    pkg = make_package()
    del pkg["paper"]["L0"]["citation_cache"]
    pkg["citation_records"] = []
    import_package(tmp_kb, pkg)
    svc = RetrievalService(tmp_kb)
    hits = svc.resolve_hint("Zhang2024DeepLearning")
    assert hits and hits[0].bibtex == ""


def test_resolve_hint_empty_kb_returns_empty_list(tmp_kb):
    svc = RetrievalService(tmp_kb)
    assert svc.resolve_hint("anything") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest literature_kb/tests/test_resolve_hint.py -v`
Expected: FAIL with `ImportError: cannot import name 'ResolvedCitation'` / `AttributeError: 'RetrievalService' object has no attribute 'resolve_hint'`.

- [ ] **Step 3: Write minimal implementation** — append to `literature_kb/kb/retrieve.py`:

```python
@dataclass(frozen=True)
class ResolvedCitation:
    """A KB-first citation resolution result for an agent-facing hint."""
    paper_id: str
    citation_key: str
    bibtex: str
    title: str
    year: str
    venue: str
    in_text: str


class RetrievalService:
    # ... existing methods unchanged ...

    def resolve_hint(self, hint: str, limit: int = 3) -> list[ResolvedCitation]:
        """Resolve a citation hint against the KB, KB-first for agents.

        Lookup order: exact citation_key -> DOI -> paper_exists -> L0 search.
        Returns top-N deduplicated candidates; never raises; [] on miss.
        """
        candidates: list[str] = []
        pid = self.store.find_by_citation_key(hint)
        if pid:
            candidates.append(pid)
        pid = self.store.find_by_doi(hint)
        if pid and pid not in candidates:
            candidates.append(pid)
        if not candidates and self.store.paper_exists(hint):
            candidates.append(hint)
        if not candidates:
            items = search.search_l0(self.store, hint, limit=limit)
            for it in items:
                if it.paper_id not in candidates:
                    candidates.append(it.paper_id)
        out: list[ResolvedCitation] = []
        for pid in candidates[:limit]:
            paper = self.store.get_paper(pid)
            if not paper:
                continue
            cache = paper.get("citation_cache") or {}
            br = paper.get("bibliographic_record") or {}
            out.append(ResolvedCitation(
                paper_id=pid,
                citation_key=paper.get("citation_key") or "",
                bibtex=cache.get("bibtex") or "",
                title=paper.get("title") or "",
                year=str(paper.get("year") or ""),
                venue=br.get("container_title") or paper.get("venue") or "",
                in_text=resolve_citation(self.store, pid)["in_text_citation"],
            ))
        return out
```

Add `from dataclasses import dataclass` to `retrieve.py`'s imports if not already present (it currently imports `from typing import Any`; add the dataclasses import at the top).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest literature_kb/tests/test_resolve_hint.py -v`
Expected: 6 PASS. Then run the full KB suite: `python -m pytest literature_kb/tests/ -q` — all pass, no regressions.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add literature_kb/kb/retrieve.py literature_kb/tests/test_resolve_hint.py && git commit -m "feat(literature_kb): add RetrievalService.resolve_hint for agent citation lookup"
```

---

### Task 2: write_agent config keys

**Files:**
- Modify: `write_agent/config.py` (add two typed accessors + one env mapping)
- Test: `tests/test_config_kb.py`

**Interfaces:**
- Produces:
  ```python
  Config.kb_path -> str | None            # KB root directory (contains kb.db); None if unset/empty
  Config.kb_discovery_per_category -> int  # default 5
  ```
  Env override `WRITING_AGENT_KB_PATH` maps to `write.kb_path`.
- Consumes: the existing `Config.get(*path, default=...)` helper and the `env_map` dict inside `load_config()`.

- [ ] **Step 1: Write the failing test** — create `tests/test_config_kb.py`:

```python
"""Config tests for the KB integration keys."""
from __future__ import annotations

from write_agent.config import load_config


def test_kb_path_default_none():
    cfg = load_config()
    assert cfg.kb_path is None


def test_kb_path_parses():
    cfg = load_config()
    cfg.data["write"]["kb_path"] = "C:/kb/data"
    assert cfg.kb_path == "C:/kb/data"


def test_kb_path_empty_string_is_none():
    cfg = load_config()
    cfg.data["write"]["kb_path"] = ""
    assert cfg.kb_path is None


def test_kb_discovery_default_is_5():
    cfg = load_config()
    assert cfg.kb_discovery_per_category == 5


def test_kb_discovery_override():
    cfg = load_config()
    cfg.data["write"]["kb_discovery_per_category"] = 8
    assert cfg.kb_discovery_per_category == 8


def test_kb_path_env_override(monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_KB_PATH", "D:/kb/data")
    cfg = load_config()
    assert cfg.kb_path == "D:/kb/data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_config_kb.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'kb_path'`.

- [ ] **Step 3: Write minimal implementation** — in `write_agent/config.py`:

Add after the `dblp_verify` property:

```python
    @property
    def kb_path(self) -> str | None:
        p = self.get("write", "kb_path", default=None)
        return p if isinstance(p, str) and p else None

    @property
    def kb_discovery_per_category(self) -> int:
        return int(self.get("write", "kb_discovery_per_category", default=5))
```

Add `"KB_PATH": ("write", "kb_path"),` to the `env_map` dict inside `load_config()` (the default string-assignment branch handles it).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_config_kb.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add write_agent/config.py tests/test_config_kb.py && git commit -m "feat(write_agent): add kb_path and kb_discovery_per_category config keys"
```

---

### Task 3: write_agent `kb_bridge` adapter

**Files:**
- Create: `write_agent/kb_bridge.py`
- Test: `tests/test_kb_bridge.py`

**Interfaces:**
- Consumes: `Config.kb_path` / `Config.kb_discovery_per_category` (Task 2); `kb.retrieve.RetrievalService` / `kb.store.KBStore` (imported lazily, optional).
- Produces:
  ```python
  @dataclass(frozen=True)
  class KbResolved:   # citation_key, bibtex, title, year, venue, in_text
  @dataclass(frozen=True)
  class KbCard:       # citation_key, title, one_line, year, in_text

  class KbProvider(Protocol):
      def resolve_hint(self, hint: str) -> list[KbResolved]: ...
      def discover_cards(self, topic: str, *, max_tokens: int, limit: int) -> list[KbCard]: ...

  class KbAdapter:  # wraps RetrievalService + KBStore behind the protocol
  def build_kb_provider(config: Config) -> KbProvider | None: ...
  ```

- [ ] **Step 1: Write the failing test** — create `tests/test_kb_bridge.py` (note the `sys.path` shim so `kb` is importable; copy the minimal `_make_package` below — `import_package` validation is lenient and only requires `L0.title`):

```python
"""Tests for write_agent.kb_bridge — the optional KB provider."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "literature_kb"))

from write_agent.config import load_config
from write_agent.kb_bridge import KbCard, KbResolved, build_kb_provider

from kb.importtool import import_package
from kb.retrieve import RetrievalService
from kb.store import KBStore


def _make_package():
    return {
        "package_spec_version": "1.0",
        "processor": {"name": "t", "version": "0.1"},
        "source": {"path": "p.pdf", "hash": None, "type": "pdf"},
        "paper": {"L0": {
            "paper_id": "",
            "title": "Deep Learning for Inverse Lithography",
            "one_line_description": "A CNN-ILT method for mask optimization.",
            "authors_summary": "Zhang et al.",
            "year": 2024,
            "venue": "Optics and Lasers in Engineering",
            "article_type": "journal",
            "doi": "10.1016/x1",
            "url": None,
            "keywords": ["ILT"], "domain_tags": ["ILT"], "method_tags": ["CNN"],
            "bibliographic_record": {
                "authors": [{"family": "Zhang", "given": "Wei"}],
                "title": "Deep Learning for Inverse Lithography",
                "container_title": "Optics and Lasers in Engineering",
                "year": 2024, "doi": "10.1016/x1",
            },
            "citation_key": "",
            "citation_cache": {
                "bibtex": "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}",
            },
        }},
        "formulas": [], "citation_records": [], "citation_graph": [],
        "validation_report": {},
    }


def _seed_kb(root: Path) -> KBStore:
    store = KBStore(root)
    store.init()
    import_package(store, _make_package())
    return store


def test_build_kb_provider_none_without_kb_path():
    cfg = load_config()
    assert build_kb_provider(cfg) is None


def test_build_kb_provider_uninitialized_dir_returns_none(tmp_path):
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path / "empty")
    assert build_kb_provider(cfg) is None


def test_adapter_resolve_hint(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path)
    provider = build_kb_provider(cfg)
    assert provider is not None
    hits = provider.resolve_hint("Zhang2024DeepLearning")
    assert len(hits) == 1
    assert isinstance(hits[0], KbResolved)
    assert hits[0].citation_key == "Zhang2024DeepLearning"
    assert "@article{zhang2024deepilt" in hits[0].bibtex


def test_adapter_discover_cards(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["write"]["kb_path"] = str(tmp_path)
    provider = build_kb_provider(cfg)
    cards = provider.discover_cards("inverse lithography", max_tokens=800, limit=5)
    assert cards
    assert isinstance(cards[0], KbCard)
    assert "lithography" in cards[0].title.lower()
    assert cards[0].year == "2024"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_kb_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'write_agent.kb_bridge'`.

- [ ] **Step 3: Write minimal implementation** — create `write_agent/kb_bridge.py`:

```python
"""Optional bridge from write_agent to the Literature KB.

The KB is a soft dependency. When `write.kb_path` is unset, or the `kb`
package is not importable, `build_kb_provider()` returns None and the
writing agent behaves exactly as before.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import Config


@dataclass(frozen=True)
class KbResolved:
    citation_key: str
    bibtex: str
    title: str
    year: str
    venue: str
    in_text: str


@dataclass(frozen=True)
class KbCard:
    citation_key: str
    title: str
    one_line: str
    year: str
    in_text: str


class KbProvider(Protocol):
    def resolve_hint(self, hint: str) -> list[KbResolved]: ...
    def discover_cards(self, topic: str, *, max_tokens: int, limit: int) -> list[KbCard]: ...


class KbAdapter:
    """Wraps literature_kb's RetrievalService behind the KbProvider protocol."""

    def __init__(self, service: Any, store: Any) -> None:
        self._service = service
        self._store = store

    def resolve_hint(self, hint: str) -> list[KbResolved]:
        return [
            KbResolved(
                citation_key=c.citation_key,
                bibtex=c.bibtex,
                title=c.title,
                year=c.year,
                venue=c.venue,
                in_text=c.in_text,
            )
            for c in self._service.resolve_hint(hint)
        ]

    def discover_cards(self, topic: str, *, max_tokens: int, limit: int) -> list[KbCard]:
        rs = self._service.retrieve(topic, "DISCOVERY", max_tokens=max_tokens)
        cards: list[KbCard] = []
        for item in rs.results:
            paper = self._store.get_paper(item.paper_id) or {}
            cards.append(KbCard(
                citation_key=item.citation_key or "",
                title=item.title or "",
                one_line=item.key_fact or "",
                year=str(paper.get("year") or ""),
                in_text=item.citation or "",
            ))
            if len(cards) >= limit:
                break
        return cards


def build_kb_provider(config: Config) -> KbProvider | None:
    """Construct the KB provider, or None when the KB is unavailable."""
    kb_path = config.kb_path
    if not kb_path:
        return None
    try:
        from kb.retrieve import RetrievalService
        from kb.store import KBStore
    except ImportError:
        # literature_kb ships as a sibling package; make it importable if present
        sibling = Path(__file__).resolve().parent.parent / "literature_kb"
        if sibling.is_dir() and str(sibling) not in sys.path:
            sys.path.insert(0, str(sibling))
        try:
            from kb.retrieve import RetrievalService
            from kb.store import KBStore
        except ImportError:
            return None
    store = KBStore(Path(kb_path))
    if not store.is_initialized:
        store.close()
        return None
    return KbAdapter(RetrievalService(store), store)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_kb_bridge.py -v`
Expected: 4 PASS. Then `python -m ruff check write_agent/` — clean.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add write_agent/kb_bridge.py tests/test_kb_bridge.py && git commit -m "feat(write_agent): add optional kb_bridge adapter and build_kb_provider factory"
```

---

### Task 4: CitationResolver KB-first

**Files:**
- Modify: `write_agent/citation.py` (constructor gains `kb`; `resolve_query` gains KB-first phase; new `_resolve_from_kb`)
- Test: `tests/test_citation_kb.py`

**Interfaces:**
- Consumes: `write_agent.kb_bridge.KbProvider`, `build_kb_provider`, `KbResolved` (Task 3); existing `_title_matches` staticmethod and `extract_cited_keys` (same module).
- Produces:
  ```python
  CitationResolver(config, timeout=20, kb: KbProvider | None = None)
  CitationResolver.kb            # provider or None (built from config when kb is None)
  CitationResolver.resolve_query(hint) -> Optional[VerifiedEntry]   # KB-first, then DBLP/CrossRef unchanged
  ```
  KB hit → `VerifiedEntry(key=<bibtex internal key>, bibtex=<verbatim>, title/year/venue=<KB>, source="KB", verified=True)`.

- [ ] **Step 1: Write the failing test** — create `tests/test_citation_kb.py`:

```python
"""Tests for CitationResolver's KB-first resolution path."""
from __future__ import annotations

from dataclasses import dataclass

from write_agent.citation import CitationResolver, VerifiedEntry
from write_agent.config import load_config


@dataclass(frozen=True)
class _FakeKbResolved:
    citation_key: str
    bibtex: str
    title: str
    year: str
    venue: str
    in_text: str


class _FakeKb:
    """A fake KbProvider: no discover_cards, configurable resolve_hint."""

    def __init__(self, hits):
        self.hits = hits

    def resolve_hint(self, hint):
        return self.hits

    def discover_cards(self, topic, *, max_tokens, limit):
        return []


_KB_BIB = "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}"


def _resolver(kb):
    return CitationResolver(load_config(), timeout=5, kb=kb)


def test_kb_hit_returns_kb_entry():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.verified
    assert e.source == "KB"
    assert e.key == "zhang2024deepilt"          # internal bibtex key, not CamelCase
    assert e.bibtex == _KB_BIB                   # verbatim
    assert e.title == "Deep Learning for Inverse Lithography"


def test_exact_citation_key_hit_bypasses_title_gate():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "A Completely Different Title", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Zhang2024DeepLearning")
    assert e is not None and e.source == "KB"


def test_title_mismatch_falls_through_to_unverified():
    hit = _FakeKbResolved("Zhang2024DeepLearning", _KB_BIB,
                          "A Completely Different Title", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")   # hint != key, title doesn't match
    assert e is not None and e.source == "UNVERIFIED"


def test_missing_bibtex_falls_through():
    hit = _FakeKbResolved("Zhang2024DeepLearning", "",
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.source == "UNVERIFIED"


def test_malformed_bibtex_falls_through():
    hit = _FakeKbResolved("Zhang2024DeepLearning", "not a bibtex",
                          "Deep Learning for Inverse Lithography", "2024", "OLE", "(Zhang et al., 2024)")
    r = _resolver(_FakeKb([hit]))
    e = r.resolve_query("Deep Learning for Inverse Lithography")
    assert e is not None and e.source == "UNVERIFIED"


def test_kb_miss_falls_through_to_unverified():
    r = _resolver(_FakeKb([]))
    e = r.resolve_query("Some Totally Unknown Paper Title")
    assert e is not None and e.source == "UNVERIFIED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_citation_kb.py -v`
Expected: FAIL — `resolve_query` has no KB path yet, so KB hits resolve to `UNVERIFIED` (title-mismatch assertions on `source` fail).

- [ ] **Step 3: Write minimal implementation** — in `write_agent/citation.py`:

Add import at top: `from .kb_bridge import KbProvider, build_kb_provider` (keep `extract_cited_keys` usage as-is — it is defined below in the same module; Python resolves it at call time).

Modify `__init__`:

```python
    def __init__(self, config: Config, timeout: int = 20,
                 kb: Optional[KbProvider] = None):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "writing-agent/0.1 (academic citation verifier)"})
        self._cache: Dict[str, VerifiedEntry] = {}
        self.kb = kb if kb is not None else build_kb_provider(config)
```

Insert a KB-first phase at the top of `resolve_query`, right after the cache check and before the DBLP search:

```python
        if query in self._cache:
            return self._cache[query]

        if self.kb is not None:
            entry = self._resolve_from_kb(query)
            if entry is not None:
                self._cache[query] = entry
                return entry
```

Add the new method after `resolve_query`:

```python
    def _resolve_from_kb(self, query: str) -> Optional[VerifiedEntry]:
        """Resolve a hint against the Literature KB, or None to fall through.

        Acceptance: non-empty BibTeX AND (the hint IS the KB citation_key OR
        the stored title passes the strict title-match gate). The draft cite
        key is the stored BibTeX's own internal key — never rewritten.
        """
        for cand in self.kb.resolve_hint(query):  # type: ignore[union-attr]
            if not cand.bibtex:
                continue
            if cand.citation_key != query and not self._title_matches(query, cand.title):
                continue
            keys = extract_cited_keys(cand.bibtex)
            if not keys:
                continue
            return VerifiedEntry(
                key=keys[0],
                bibtex=cand.bibtex,
                title=cand.title,
                year=cand.year,
                venue=cand.venue,
                source="KB",
                verified=True,
            )
        return None
```

(Add the `# type: ignore[union-attr]` only if type-checkers flag `self.kb`; the guard above ensures it is not None.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_citation_kb.py -v`
Expected: 6 PASS. Then `python -m pytest tests/ -q` (existing smoke suite) — all pass.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add write_agent/citation.py tests/test_citation_kb.py && git commit -m "feat(write_agent): resolve citation hints KB-first with title and bibtex gates"
```

---

### Task 5: Pipeline wiring

**Files:**
- Modify: `write_agent/pipeline.py` (construct resolver when `dblp_verify or kb_path`)
- Test: `tests/test_pipeline_kb.py`

**Interfaces:**
- Consumes: `Config.kb_path` (Task 2), `CitationResolver` KB-first constructor (Task 4).
- Produces: `Pipeline.citation_resolver` is non-None when either `dblp_verify` or `kb_path` is set; its `.kb` provider reflects the KB.

- [ ] **Step 1: Write the failing test** — create `tests/test_pipeline_kb.py`:

```python
"""Pipeline wiring for the optional KB provider."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "literature_kb"))

from write_agent.config import load_config
from write_agent.pipeline import Pipeline

from kb.importtool import import_package


def _seed_kb(root: Path) -> None:
    pkg = {
        "package_spec_version": "1.0",
        "processor": {"name": "t", "version": "0.1"},
        "source": {"path": "p.pdf", "hash": None, "type": "pdf"},
        "paper": {"L0": {
            "paper_id": "", "title": "Deep Learning for Inverse Lithography",
            "one_line_description": "A CNN-ILT method.", "authors_summary": "Zhang et al.",
            "year": 2024, "venue": "OLE", "article_type": "journal",
            "doi": "10.1016/x1", "url": None,
            "keywords": ["ILT"], "domain_tags": ["ILT"], "method_tags": ["CNN"],
            "bibliographic_record": {
                "authors": [{"family": "Zhang", "given": "Wei"}],
                "title": "Deep Learning for Inverse Lithography",
                "container_title": "OLE", "year": 2024, "doi": "10.1016/x1",
            },
            "citation_key": "",
            "citation_cache": {"bibtex": "@article{zhang2024deepilt,\n  title = {Deep Learning for Inverse Lithography},\n  year = {2024}\n}"},
        }},
        "formulas": [], "citation_records": [], "citation_graph": [],
        "validation_report": {},
    }
    from kb.store import KBStore
    store = KBStore(root)
    store.init()
    import_package(store, pkg)


def test_pipeline_no_kb_no_resolver():
    cfg = load_config()
    cfg.data["write"]["dblp_verify"] = False
    p = Pipeline(cfg, verbose=False)
    assert p.citation_resolver is None


def test_pipeline_kb_only_builds_resolver(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["write"]["dblp_verify"] = False
    cfg.data["write"]["kb_path"] = str(tmp_path)
    p = Pipeline(cfg, verbose=False)
    assert p.citation_resolver is not None
    assert p.citation_resolver.kb is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_pipeline_kb.py -v`
Expected: `test_pipeline_kb_only_builds_resolver` FAILS — today the resolver is only built when `dblp_verify` is on, so `p.citation_resolver is None`.

- [ ] **Step 3: Write minimal implementation** — in `write_agent/pipeline.py`, line 34:

```python
        self.citation_resolver = (
            CitationResolver(config)
            if (config.dblp_verify or config.kb_path)
            else None
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_pipeline_kb.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add write_agent/pipeline.py tests/test_pipeline_kb.py && git commit -m "feat(write_agent): build CitationResolver when dblp_verify or kb_path is set"
```

---

### Task 6: Related-work grounding (prompts + write phase)

**Files:**
- Modify: `write_agent/prompts.py` (add `{kb_cards}` block to `WRITE_SECTION_SPECIFIC_RELATED`)
- Modify: `write_agent/phases/write.py` (helpers + `_write_generic_section` `kb_cards` param + `run_write` wiring)
- Test: `tests/test_related_work_kb.py`

**Interfaces:**
- Consumes: `citation_resolver.kb` (a `KbProvider`, Task 4), `Config.kb_discovery_per_category` (Task 2), existing `WRITE_SECTION_SPECIFIC_RELATED` template.
- Produces:
  ```python
  related_work_topics(section: Dict[str, Any]) -> List[str]
  format_kb_cards(cards: List[Tuple[Any, str]]) -> str          # [(KbCard, draft_key)]
  ground_related_work(provider, section, config, resolver, citation_keys, resolved_entries, seen_hints) -> str
  _write_generic_section(..., kb_cards: str = "")               # RELATED template formats {kb_cards}
  ```
  `run_write` grows `citation_keys` / `resolved_entries` in place during the related-work section.

- [ ] **Step 1: Write the failing test** — create `tests/test_related_work_kb.py`:

```python
"""Write-phase related-work grounding tests (fake provider + MockLLM)."""
from __future__ import annotations

from dataclasses import dataclass

from write_agent.config import load_config
from write_agent.phases.write import (
    format_kb_cards,
    ground_related_work,
    related_work_topics,
)
from write_agent.citation import CitationResolver


@dataclass(frozen=True)
class _Card:
    citation_key: str
    title: str
    one_line: str
    year: str
    in_text: str


class _FakeKb:
    def __init__(self, cards):
        self.cards = cards

    def resolve_hint(self, hint):
        # KB-first resolution: match by title token overlap
        from write_agent.citation import VerifiedEntry, extract_cited_keys
        for c in self.cards:
            if c.title.lower() in hint.lower():
                bib = f"@article{{{c.citation_key.lower()},\n  title = {{{c.title}}},\n  year = {{{c.year}}}\n}}"
                return [type("R", (), {
                    "citation_key": c.citation_key, "bibtex": bib,
                    "title": c.title, "year": c.year, "venue": "OLE", "in_text": c.in_text,
                })()]
        return []

    def discover_cards(self, topic, *, max_tokens, limit):
        return [c for c in self.cards][:limit]


def test_related_work_topics_deduplicates():
    section = {"citations_hint": ["Title A", "Title A", ""],
               "key_points": ["synthesize", "Title A"]}
    topics = related_work_topics(section)
    assert topics == ["Title A", "synthesize"]


def test_format_kb_cards():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    out = format_kb_cards([(card, "zhang2024deepilt")])
    assert "[zhang2024deepilt]" in out
    assert "Deep Learning for Inverse Lithography" in out
    assert "A CNN-ILT method." in out
    assert format_kb_cards([]) == ""


def test_ground_related_work_enqueues_and_resolves():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    cfg = load_config()
    kb = _FakeKb([card])
    resolver = CitationResolver(cfg, timeout=5, kb=kb)
    section = {"title": "Related Work", "citations_hint": [], "key_points": ["inverse lithography"]}
    keys, entries, seen = [], [], set()
    block = ground_related_work(kb, section, cfg, resolver, keys, entries, seen)
    assert keys == ["zhang2024deepilt"]
    assert len(entries) == 1 and entries[0].source == "KB"
    assert "[zhang2024deepilt]" in block
    assert "Deep Learning for Inverse Lithography" in block


def test_ground_related_work_skips_already_seen():
    card = _Card("Zhang2024DeepLearning", "Deep Learning for Inverse Lithography",
                 "A CNN-ILT method.", "2024", "(Zhang et al., 2024)")
    cfg = load_config()
    kb = _FakeKb([card])
    resolver = CitationResolver(cfg, timeout=5, kb=kb)
    section = {"title": "Related Work", "citations_hint": [], "key_points": ["x"]}
    keys, entries, seen = [], [], {card.title}
    block = ground_related_work(kb, section, cfg, resolver, keys, entries, seen)
    assert keys == [] and block == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_related_work_kb.py -v`
Expected: FAIL with `ImportError: cannot import name 'related_work_topics'` (helpers don't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `write_agent/prompts.py`, change `WRITE_SECTION_SPECIFIC_RELATED` — insert before the line `Return ONLY the LaTeX body text.`:

```python
KB KNOWN WORK AVAILABLE (from your curated library — cite ONLY keys listed in
CITATION KEYS AVAILABLE; cards whose key is absent may be described but not cited):
{kb_cards}

Return ONLY the LaTeX body text.
```

In `write_agent/phases/write.py`:

1. Add module-level helpers (place after `build_section_spec`):

```python
def related_work_topics(section: Dict[str, Any]) -> List[str]:
    """Category/topic hints for a section, deduplicated and order-preserving."""
    out: List[str] = []
    for h in section.get("citations_hint", []) + section.get("key_points", []):
        if h and h not in out:
            out.append(h)
    return out


def format_kb_cards(cards: List[Tuple[Any, str]]) -> str:
    """Render citable KB cards as a prompt block. cards = [(KbCard, draft_key), ...]."""
    if not cards:
        return ""
    return "\n".join(
        f"- [{key}] {card.title} ({card.year}) — {card.one_line}"
        for card, key in cards
    )


def ground_related_work(
    provider: Any,
    section: Dict[str, Any],
    config: Config,
    resolver: CitationResolver,
    citation_keys: List[str],
    resolved_entries: List[Any],
    seen_hints: set,
) -> str:
    """Ground related-work writing in KB cards.

    For each topic, DISCOVERY cards are enqueued as citation hints and
    resolved through the (KB-first) resolver. Only citable cards (resolved,
    with BibTeX) are formatted. Mutates citation_keys / resolved_entries in
    place so the final write_bibliography picks them up.
    """
    citable: List[Tuple[Any, str]] = []
    limit = config.kb_discovery_per_category
    for topic in related_work_topics(section):
        for card in provider.discover_cards(topic, max_tokens=800, limit=limit):
            if card.title in seen_hints:
                continue
            seen_hints.add(card.title)
            entry = resolver.resolve_query(card.title)
            if entry and entry.verified:
                if entry.key not in citation_keys:
                    citation_keys.append(entry.key)
                    resolved_entries.append(entry)
                citable.append((card, entry.key))
    return format_kb_cards(citable)
```

2. Change `_write_generic_section` signature to `..., citation_keys: List[str], kb_cards: str = "")` and add the RELATED branch in the `user = template.format(...)` block:

```python
    elif template is prompts.WRITE_SECTION_SPECIFIC_RELATED:
        user = template.format(
            paper_context=paper_context,
            section_spec=section_spec,
            written_so_far=written_so_far,
            target_pages=section.get("target_pages", 1.0),
            citation_keys=keys_str,
            kb_cards=kb_cards or "NONE",
        )
```

3. In `run_write`, restructure the pre-resolution block so `seen_hints` escapes, then wire grounding into the section loop. Replace the block from `citation_keys: List[str] = []` through the end of the pre-resolution `if config.dblp_verify ...` with:

```python
    citation_keys: List[str] = []
    resolved_entries = []
    seen_hints: set = set()
    if citation_resolver is not None:
        hint_list = []
        for s in plan.sections:
            hint_list.extend(s.get("citations_hint", []))
        for k, v in (plan.citation_plan or {}).items():
            if isinstance(v, list):
                hint_list.extend(v)
        for h in hint_list:
            if h and h not in seen_hints:
                seen_hints.add(h)
                entry = citation_resolver.resolve_query(h)
                if entry and entry.verified:
                    resolved_entries.append(entry)
                    citation_keys.append(entry.key)
```

Then in the section loop, before the `if sid == "0":` branch, add:

```python
        kb_cards = ""
        if (
            sid != "0"
            and citation_resolver is not None
            and citation_resolver.kb is not None
            and "related" in (section.get("title") or "").lower()
        ):
            kb_cards = ground_related_work(
                citation_resolver.kb, section, config, citation_resolver,
                citation_keys, resolved_entries, seen_hints,
            )
```

and pass it to `_write_generic_section`:

```python
            body = _write_generic_section(
                client, config, paper_context, section, written_so_far,
                citation_keys, kb_cards=kb_cards,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_related_work_kb.py -v`
Expected: 4 PASS. Then `python -m pytest tests/ -q` — existing smoke suite still passes (the `dblp_verify=False` smoke test has `citation_resolver is None`, so no grounding runs).

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add write_agent/prompts.py write_agent/phases/write.py tests/test_related_work_kb.py && git commit -m "feat(write_agent): ground related-work prose in KB discovery cards"
```

---

### Task 7: End-to-end KB-first regression

**Files:**
- Test: `tests/test_pipeline_kb.py` (extend the file from Task 5)
- No production code changes.

**Interfaces:**
- Consumes: full Task 1–6 stack.
- Produces: a regression proving a KB-only full pipeline run (no network) resolves a citation hint from the KB, injects the KB cards block into the related-work prompt, and writes a compilable `references.bib`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_pipeline_kb.py`:

```python
class _MockResult:
    def __init__(self, text):
        self.text = text
        self.usage_input = 0
        self.usage_output = 0
        self.model = "mock"


class _MockClient:
    """Duck-typed DeepSeekClient that records prompts and returns canned output."""

    def __init__(self):
        self.calls = []

    def chat(self, system, user, temperature=None, max_tokens=None, stop=None):
        self.calls.append(("chat", user))
        if "Run the final scientific writing quality audit" in user:
            return _MockResult('{"issues": [], "passes_clean": [true,true,true,true,true,true,true,true,true,true,true,true,true], "overall": "clean"}')
        if "You are reviewing an academic paper" in user:
            return _MockResult('{"score": 7.0, "summary": "ok", "strengths": ["s"], "weaknesses": [], "verdict": "ready"}')
        if "BEGIN FILE:" in user:
            return _MockResult("===== BEGIN FILE: 0_abstract.tex =====\nabs\n===== END FILE: 0_abstract.tex =====\n" +
                               "===== BEGIN FILE: 1_introduction.tex =====\nintro\n===== END FILE: 1_introduction.tex =====\n" +
                               "===== BEGIN FILE: 2_related_work.tex =====\nrelated\n===== END FILE: 2_related_work.tex =====\n" +
                               "===== BEGIN FILE: 3_method.tex =====\nmethod\n===== END FILE: 3_method.tex =====\n" +
                               "===== BEGIN FILE: 4_experiments.tex =====\nexp\n===== END FILE: 4_experiments.tex =====\n" +
                               "===== BEGIN FILE: 5_conclusion.tex =====\nconc\n===== END FILE: 5_conclusion.tex =====\n")
        return _MockResult("Draft section content.")

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        self.calls.append(("chat_json", user))
        if "building the paper outline" in user:
            return {
                "paper_type": "empirical",
                "sections": [
                    {"id": "0", "title": "Abstract", "filename": "0_abstract.tex", "purpose": "summary", "key_points": [], "target_pages": 0.3},
                    {"id": "1", "title": "Introduction", "filename": "1_introduction.tex", "purpose": "motivation", "key_points": ["hook"], "citations_hint": [], "target_pages": 1.5},
                    {"id": "2", "title": "Related Work", "filename": "2_related_work.tex", "purpose": "positioning",
                     "key_points": ["inverse lithography"], "citations_hint": ["Deep Learning for Inverse Lithography (Zhang et al., 2024)"], "target_pages": 1.0},
                    {"id": "3", "title": "Method", "filename": "3_method.tex", "purpose": "approach", "key_points": ["formulation"], "citations_hint": [], "target_pages": 2.0},
                    {"id": "4", "title": "Experiments", "filename": "4_experiments.tex", "purpose": "results", "key_points": ["setup"], "citations_hint": [], "target_pages": 3.0},
                    {"id": "5", "title": "Conclusion", "filename": "5_conclusion.tex", "purpose": "wrap up", "key_points": ["contributions"], "citations_hint": [], "target_pages": 0.5},
                ],
                "figure_plan": [{"id": "fig1", "type": "plot", "description": "x", "data_source": "exp1"}],
                "citation_plan": {"intro": [], "related": ["Deep Learning for Inverse Lithography (Zhang et al., 2024)"], "method": []},
            }
        return {}

    def chat_json_list(self, system, user, temperature=None, max_tokens=None):
        return []


def test_kb_only_full_pipeline(tmp_path):
    _seed_kb(tmp_path)
    cfg = load_config()
    cfg.data["pipeline"]["output_dir"] = str(tmp_path / "out")
    cfg.data["model"]["name"] = "mock"
    cfg.data["review"]["max_rounds"] = 1
    cfg.data["review"]["min_score"] = 6.0
    cfg.data["write"]["dblp_verify"] = False
    cfg.data["write"]["kb_path"] = str(tmp_path)

    client = _MockClient()
    pipeline = Pipeline(cfg, verbose=False, client=client)  # type: ignore[arg-type]
    report = pipeline.run(source="A CNN-ILT method", max_rounds=1)
    assert "error" not in report, report.get("error")

    bib = (Path(cfg.output_dir) / "paper" / "references.bib").read_text(encoding="utf-8")
    assert "zhang2024deepilt" in bib, "KB-resolved entry missing from references.bib"
    assert "@article{zhang2024deepilt" in bib

    prompts_text = "".join(user for (_, user) in client.calls)
    assert "KB KNOWN WORK AVAILABLE" in prompts_text, "related-work KB cards block missing"
    assert "Deep Learning for Inverse Lithography" in prompts_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd writing-agent && python -m pytest tests/test_pipeline_kb.py::test_kb_only_full_pipeline -v`
Expected: FAIL (either the KB cards block is absent from the prompt, or the KB bibtex is missing from `references.bib`) — proving the grounding + KB-first path is not wired end-to-end yet.

- [ ] **Step 3: Implement** — no production code changes needed if Tasks 1–6 are complete; this step is the verification that the whole stack works together.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd writing-agent && python -m pytest tests/test_pipeline_kb.py -q`
Expected: 3 PASS (2 from Task 5 + this one). Then run everything:

```bash
cd writing-agent && python -m pytest paper2kb/tests/ literature_kb/tests/ tests/ -q
python -m ruff check paper2kb/ literature_kb/ write_agent/
```

All green, ruff clean.

- [ ] **Step 5: Commit**

```bash
cd writing-agent && git add tests/test_pipeline_kb.py && git commit -m "test(write_agent): end-to-end KB-only pipeline regression"
```

---

## Self-Review Notes

- **Spec coverage**: `resolve_hint` (PRD §Contract-KB) → Task 1. Config keys → Task 2. Provider protocol + `build_kb_provider` + degradation ladder → Task 3. KB-first `resolve_query` with title/bibtex gates + verbatim-key strategy + mode matrix (`dblp_verify or kb_path`) → Tasks 4–5. `{kb_cards}` prompt + discovery-per-category + auto-enqueue + citable-only injection + in-place list growth → Task 6. End-to-end regression + 30-refs/no-orphan guard → Task 7.
- **Placeholder scan**: every step has concrete code; no "TBD"/"add error handling" placeholders.
- **Type consistency**: `KbResolved`/`KbCard` field names match between Task 3 (definition) and Task 4 (`_resolve_from_kb`) and Task 6 (`format_kb_cards`); `resolve_hint(hint) -> list[...]` signature identical in `ResolvedCitation` (Task 1) and `KbResolved` (Task 3); `citation_resolver.kb` is the single provider accessor used in Tasks 4–6.
