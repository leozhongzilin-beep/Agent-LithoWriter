"""Pipeline orchestrator (Skill §3): parse -> metadata -> layers -> validate -> emit.

Each layer is one LLM call; failures on a single layer abort cleanly so the
operator can inspect the offending response rather than receive a partial KB
entry. `llm` is injected (a DeepSeekClient in production, a mock in tests).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import __version__, emit, extractors, metadata, parser, validate


class Paper2KBError(Exception):
    """Raised when a paper cannot be processed (parse/LLM/validation)."""


def _source_hash(path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_paper(
    source_path,
    *,
    llm,
    doi: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Parse a source and produce a canonical KB package dict."""
    doc = parser.parse_source(source_path)
    record = metadata.resolve_metadata(doi=doi, title=title or doc.title_hint)
    pdf_meta = _pdf_metadata(source_path) if doc.source_type == "pdf" else None
    meta = metadata.merge_metadata(
        record, pdf_meta, title_override=title or doc.title_hint)

    l0 = extractors.extract_l0(llm, doc, meta)
    l1 = extractors.extract_l1(llm, doc)
    l2m = extractors.extract_l2_method(llm, doc)
    l2r = extractors.extract_l2_results(llm, doc)
    l3 = extractors.extract_l3(llm, doc)
    formulas = extractors.extract_formulas(llm, doc)
    graph = extractors.extract_citation_graph(llm, doc)

    package = emit.assemble(
        doc, meta, l0, l1, l2m, l2r, l3, formulas, graph,
        source_path=str(Path(source_path).resolve()),
        source_hash=_source_hash(source_path),
        version=__version__,
    )
    package["validation_report"] = validate.validate(package)
    return package


def _pdf_metadata(path) -> dict[str, Any] | None:
    import fitz

    try:
        doc = fitz.open(str(path))
        m = doc.metadata or {}
        doc.close()
        return {"title": m.get("title") or "", "author": m.get("author") or ""}
    except (RuntimeError, ValueError, OSError):
        return None  # damaged/corrupt PDF -> metadata degrades, parse still works


def make_llm(model: str | None = None, **kwargs):
    """Production LLM client (DeepSeek). Raises on a missing API key."""
    from write_agent.llm import DeepSeekClient  # type: ignore

    from ._client import api_key, base_url  # type: ignore

    return DeepSeekClient(api_key=api_key(), base_url=base_url(), model=model, **kwargs)
