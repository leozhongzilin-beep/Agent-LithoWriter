"""Tests for source parsing (paper2kb/parser.py)."""

from __future__ import annotations

import pytest
from paper2kb.parser import ParsedDoc, UnsupportedFormat, parse_source

_MD = (
    "# Deep Learning for Inverse Lithography\n\n"
    "Intro paragraph.\n\n"
    "## Method\n\n"
    "Method paragraph.\n\n"
    "## Experiments\n\n"
    "Results paragraph.\n"
)


def _make_pdf(path, pages: list[str]):
    import fitz
    d = fitz.open()
    for text in pages:
        p = d.new_page()
        p.insert_text((72, 72), text, fontsize=11)
    d.save(path)
    d.close()


def test_parse_markdown_sections_and_title(tmp_path):
    src = tmp_path / "paper.md"
    src.write_text(_MD, encoding="utf-8")
    doc = parse_source(src)
    assert isinstance(doc, ParsedDoc)
    assert doc.source_type == "md"
    assert doc.title_hint == "Deep Learning for Inverse Lithography"
    headings = [s.heading for s in doc.sections]
    assert "Method" in headings and "Experiments" in headings
    assert "Results paragraph" in doc.full_text


def test_parse_latex(tmp_path):
    src = tmp_path / "paper.tex"
    src.write_text(
        r"\title{A KAN Paper}" + "\n\n"
        r"\section{Method}" + "\n\nMethod text.\n\n"
        r"\section{Results}" + "\n\nResult text.\n",
        encoding="utf-8",
    )
    doc = parse_source(src)
    assert doc.source_type == "latex"
    assert doc.title_hint == "A KAN Paper"
    assert [s.heading for s in doc.sections] == ["Method", "Results"]


def test_parse_pdf_extracts_pages(tmp_path):
    src = tmp_path / "paper.pdf"
    _make_pdf(src, ["Introduction text here.", "Method details here."])
    doc = parse_source(src)
    assert doc.source_type == "pdf"
    assert "Introduction text" in doc.pages[1]
    assert "Method details" in doc.pages[2]
    assert "[p1]" in doc.full_text  # page anchors embedded for the LLM


def test_parse_unknown_format_raises(tmp_path):
    src = tmp_path / "paper.xyz"
    src.write_text("hi", encoding="utf-8")
    with pytest.raises(UnsupportedFormat):
        parse_source(src)


def test_parse_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_source(tmp_path / "nope.pdf")
