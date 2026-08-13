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
