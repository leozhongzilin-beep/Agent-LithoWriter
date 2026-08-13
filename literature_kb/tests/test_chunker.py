"""Tests for L4 paragraph chunking (kb/chunker.py)."""

from __future__ import annotations

import json

import pytest
from kb import chunker
from kb.importtool import import_package

# ---------------------------------------------------------------------------
# chunk_markdown
# ---------------------------------------------------------------------------

def test_chunk_markdown_sections_and_paragraphs():
    text = (
        "# Deep Learning for Inverse Lithography\n\n"
        "Intro paragraph one.\n\n"
        "## Method\n\n"
        "Method paragraph one.\n\n"
        "Method paragraph two.\n\n"
        "## Results\n\n"
        "Results paragraph.\n"
    )
    doc = chunker.chunk_markdown(text, "ILT_2024_001")
    assert doc.title == "Deep Learning for Inverse Lithography"
    assert doc.sections == ["Method", "Results"]
    # preamble (intro) + 2 method + 1 results = 4 chunks
    assert len(doc.chunks) == 4
    assert doc.chunks[0].section == "Preamble"
    assert doc.chunks[1].section == "Method"
    assert doc.chunks[2].section == "Method"
    assert doc.chunks[3].section == "Results"
    # paragraph_index is global and sequential
    assert [c.paragraph_index for c in doc.chunks] == [1, 2, 3, 4]
    # chunk ids are deterministic
    assert doc.chunks[0].chunk_id == "ILT_2024_001.ch001"


def test_chunk_markdown_blank_text_has_no_chunks():
    doc = chunker.chunk_markdown("", "ILT_2024_001")
    assert doc.chunks == []
    assert doc.sections == []


# ---------------------------------------------------------------------------
# chunk_source (format detection)
# ---------------------------------------------------------------------------

def test_chunk_source_markdown_file(tmp_path):
    src = tmp_path / "paper.md"
    src.write_text(
        "# Title\n\nIntro.\n\n## Method\n\nPara.\n", encoding="utf-8"
    )
    doc = chunker.chunk_source("ILT_2024_001", src)
    assert doc is not None
    assert doc.title == "Title"
    assert len(doc.chunks) == 2


def test_chunk_latex_sections(tmp_path):
    src = tmp_path / "paper.tex"
    src.write_text(
        r"\title{A KAN Paper}" + "\n\n"
        r"\section{Method}" + "\n\nMethod text.\n\n"
        r"\section{Results}" + "\n\nResult text.\n",
        encoding="utf-8",
    )
    doc = chunker.chunk_source("ILT_2024_001", src)
    assert doc.title == "A KAN Paper"          # from \title{}, not \section
    assert doc.sections == ["Method", "Results"]
    assert len(doc.chunks) == 2


def test_chunk_source_pdf_is_deferred(tmp_path):
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"pdf")
    with pytest.raises(chunker.UnsupportedFormat, match="PDF"):
        chunker.chunk_source("ILT_2024_001", src)


def test_chunk_source_unknown_format(tmp_path):
    src = tmp_path / "paper.xyz"
    src.write_text("hello", encoding="utf-8")
    with pytest.raises(chunker.UnsupportedFormat):
        chunker.chunk_source("ILT_2024_001", src)


# ---------------------------------------------------------------------------
# store_chunks (paper_fulltext)
# ---------------------------------------------------------------------------

def test_store_chunks_writes_paper_fulltext(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    doc = chunker.chunk_markdown(
        "# T\n\nIntro.\n\n## Method\n\nPara.\n", "ILT_2024_001")
    chunker.store_chunks(tmp_kb, "ILT_2024_001", doc)
    row = tmp_kb.conn.execute(
        "SELECT chunk_available, section_index, chunks FROM paper_fulltext "
        "WHERE paper_id = 'ILT_2024_001'"
    ).fetchone()
    assert row["chunk_available"] == 1
    assert json.loads(row["section_index"]) == ["Method"]
    chunks = json.loads(row["chunks"])
    assert chunks[0]["section"] == "Preamble"
    assert chunks[0]["text"] == "Intro."


def test_store_chunks_is_idempotent_and_replaces(tmp_kb, make_package):
    import_package(tmp_kb, make_package())
    chunker.store_chunks(tmp_kb, "ILT_2024_001",
                         chunker.chunk_markdown("# T\n\nA.\n", "ILT_2024_001"))
    chunker.store_chunks(tmp_kb, "ILT_2024_001",
                         chunker.chunk_markdown("# T\n\nB.\n", "ILT_2024_001"))
    row = tmp_kb.conn.execute(
        "SELECT chunks FROM paper_fulltext WHERE paper_id='ILT_2024_001'"
    ).fetchone()
    assert json.loads(row["chunks"])[0]["text"] == "B."  # replaced, not appended
