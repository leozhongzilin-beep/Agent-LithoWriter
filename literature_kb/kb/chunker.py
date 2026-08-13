"""L4 paragraph chunking (PRD KB-Completion group A).

Parses an archived source document (markdown/text/XML/LaTeX now; PDF deferred
via PyMuPDF) into section-titled paragraph chunks stored in `paper_fulltext`,
making the retrieval router's L4 escalation real. Self-contained — no
dependency on external converters.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import fts
from .ids import format_sub_id
from .store import KBStore

_PREAMBLE = "Preamble"
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+)$")
_LATEX_SECTION = re.compile(r"^\\(sub){0,2}section\*?\{\s*([^}]+)\s*\}")
_XML_SEC = re.compile(r"<sec[^>]*title=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_XML_P = re.compile(r"<p>(.*?)</p>", re.DOTALL | re.IGNORECASE)


class UnsupportedFormat(Exception):
    """Raised when a source document's format cannot be chunked yet."""


@dataclass
class Chunk:
    chunk_id: str
    section: str
    paragraph_index: int
    text: str
    page: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChunkDoc:
    paper_id: str
    title: str = ""
    sections: list[str] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


# ---------------------------------------------------------------------------
# per-format chunkers
# ---------------------------------------------------------------------------

def chunk_markdown(text: str, paper_id: str) -> ChunkDoc:
    """Split markdown into sections (H2+) and blank-line-separated paragraphs.

    The first H1 becomes the document title. Text before any H2 lands in the
    Preamble section.
    """
    doc_title = ""
    sections: list[str] = []
    current = _PREAMBLE
    chunks: list[Chunk] = []
    para: list[str] = []
    pi = 0

    def flush() -> None:
        nonlocal para, pi
        body = " ".join(p.strip() for p in para).strip()
        if body:
            pi += 1
            chunks.append(Chunk(
                chunk_id=format_sub_id(paper_id, "chunk", pi),
                section=current,
                paragraph_index=pi,
                text=body,
            ))
        para = []

    for line in text.split("\n"):
        stripped = line.strip()
        m = _MD_HEADER.match(stripped)
        if m:
            flush()
            level = len(m.group(1))
            heading = m.group(2).strip()
            if level == 1 and not doc_title and not chunks:
                doc_title = heading
                continue
            current = heading
            if heading not in sections:
                sections.append(heading)
        elif not stripped:
            flush()
        else:
            para.append(stripped)
    flush()

    return ChunkDoc(paper_id=paper_id, title=doc_title, sections=sections,
                    chunks=chunks)


def chunk_latex(text: str, paper_id: str) -> ChunkDoc:
    """Split LaTeX by \\section / \\subsection into section-titled chunks."""
    doc_title = ""
    sections: list[str] = []
    current = _PREAMBLE
    chunks: list[Chunk] = []
    para: list[str] = []
    pi = 0

    def flush() -> None:
        nonlocal para, pi
        body = " ".join(p.strip() for p in para).strip()
        if body:
            pi += 1
            chunks.append(Chunk(
                chunk_id=format_sub_id(paper_id, "chunk", pi),
                section=current, paragraph_index=pi, text=body,
            ))
        para = []

    for raw in text.split("\n"):
        line = raw.strip()
        m = _LATEX_SECTION.match(line)
        if m:
            flush()
            current = m.group(2).strip()  # every \section is a real section
            if current not in sections:
                sections.append(current)
        elif line.startswith("\\title"):
            tm = re.search(r"\\title\*?\{([^}]+)\}", line)
            if tm:
                doc_title = tm.group(1).strip()
        elif line and not line.startswith(("\\begin", "\\end", "\\label", "%")):
            para.append(line)
        elif not line:
            flush()
    flush()
    return ChunkDoc(paper_id=paper_id, title=doc_title, sections=sections,
                    chunks=chunks)


def chunk_xml(text: str, paper_id: str) -> ChunkDoc:
    """Split simple XML by <sec title=...>/<p> blocks (best-effort)."""
    sections: list[str] = []
    chunks: list[Chunk] = []
    current = _PREAMBLE
    pi = 0

    for m in _XML_P.finditer(text):
        body = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not body:
            continue
        # a <sec> title immediately before this <p> becomes the section
        for sm in _XML_SEC.finditer(text[: m.start()][-400:]):
            current = sm.group(1).strip()
            if current not in sections:
                sections.append(current)
        pi += 1
        chunks.append(Chunk(
            chunk_id=format_sub_id(paper_id, "chunk", pi),
            section=current, paragraph_index=pi, text=body,
        ))
    return ChunkDoc(paper_id=paper_id, sections=sections, chunks=chunks)


# ---------------------------------------------------------------------------
# dispatch + storage
# ---------------------------------------------------------------------------

def chunk_source(paper_id: str, source_path) -> ChunkDoc:
    """Detect the source format and chunk it. PDF is explicitly deferred."""
    path = Path(source_path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".md", ".markdown", ".txt"):
        return chunk_markdown(text, paper_id)
    if suffix in (".tex", ".latex"):
        return chunk_latex(text, paper_id)
    if suffix == ".xml":
        return chunk_xml(text, paper_id)
    if suffix == ".pdf":
        raise UnsupportedFormat(
            "PDF chunking is deferred (PyMuPDF reader is a follow-on); "
            "provide a markdown/text/XML/LaTeX source")
    raise UnsupportedFormat(f"unsupported source format: {suffix}")


def store_chunks(store: KBStore, paper_id: str, doc: ChunkDoc) -> None:
    """Write chunks + section_index into paper_fulltext, chunk_available=1.

    The FTS chunk index is synced in the same transaction so chunked papers are
    immediately searchable through L4.
    """
    chunks = [c.to_dict() for c in doc.chunks]
    chunks_json = json.dumps(chunks, ensure_ascii=False)
    sections_json = json.dumps(doc.sections, ensure_ascii=False)
    with store.conn:
        fts.sync_chunks(store.conn, paper_id, chunks)
        store.conn.execute(
            "INSERT INTO paper_fulltext (paper_id, fulltext_pointer, section_index, "
            "chunk_available, chunks) VALUES (?, NULL, ?, 1, ?) "
            "ON CONFLICT(paper_id) DO UPDATE SET "
            "section_index=excluded.section_index, "
            "chunk_available=1, chunks=excluded.chunks",
            (paper_id, sections_json, chunks_json),
        )


def get_chunks(store: KBStore, paper_id: str) -> list[dict[str, Any]]:
    """Chunks for a paper (empty if not chunked yet)."""
    row = store.conn.execute(
        "SELECT chunks FROM paper_fulltext WHERE paper_id = ? AND chunk_available = 1",
        (paper_id,),
    ).fetchone()
    if row is None or not row["chunks"]:
        return []
    return json.loads(row["chunks"])
