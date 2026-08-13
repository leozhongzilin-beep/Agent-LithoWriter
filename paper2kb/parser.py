"""Source document parsing (Skill step 1).

Parses PDF / markdown / XML / LaTeX / text into section-tagged paragraphs with
page anchors where available. The LLM layer does the semantic section
identification (Skill step 4); this module's job is to hand the LLM clean,
page-anchored text without losing evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+)$")
_LATEX_SECTION = re.compile(r"^\\(sub){0,2}section\*?\{\s*([^}]+)\s*\}")


class UnsupportedFormat(Exception):
    """Raised when a source file's format cannot be parsed."""


@dataclass
class ParsedSection:
    heading: str
    text: str


@dataclass
class ParsedDoc:
    source_type: str  # pdf | md | latex | xml | txt
    title_hint: str = ""
    sections: list[ParsedSection] = field(default_factory=list)
    full_text: str = ""
    pages: dict[int, str] = field(default_factory=dict)


def parse_source(path) -> ParsedDoc:
    """Detect the format and parse the source into a ParsedDoc."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"source not found: {p}")
    suffix = p.suffix.lower()
    text = p.read_text(encoding="utf-8", errors="replace")
    if suffix in (".md", ".markdown"):
        return parse_markdown(text, "md")
    if suffix == ".txt":
        return _plain_text(text)
    if suffix in (".tex", ".latex"):
        return parse_latex(text)
    if suffix == ".xml":
        return parse_xml(text)
    if suffix == ".pdf":
        return parse_pdf(p)
    raise UnsupportedFormat(f"unsupported source format: {suffix}")


def _plain_text(text: str) -> ParsedDoc:
    paras = [b.strip() for b in text.split("\n\n") if b.strip()]
    return ParsedDoc(source_type="txt", title_hint=paras[0] if paras else "",
                     sections=[], full_text=text)


def parse_markdown(text: str, source_type: str = "md") -> ParsedDoc:
    """Split markdown into H2+ sections; the first H1 becomes the title."""
    title = ""
    sections: list[ParsedSection] = []
    current = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        body = " ".join(b.strip() for b in buf).strip()
        if body:
            sections.append(ParsedSection(heading=current, text=body))
        buf = []

    for line in text.split("\n"):
        m = _MD_HEADER.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            heading = m.group(2).strip()
            if level == 1 and not title:
                title = heading
                continue
            current = heading
        else:
            buf.append(line)
    flush()
    return ParsedDoc(source_type=source_type, title_hint=title,
                     sections=sections, full_text=text)


def parse_latex(text: str) -> ParsedDoc:
    title = ""
    sections: list[ParsedSection] = []
    current = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        body = " ".join(b.strip() for b in buf).strip()
        if body:
            sections.append(ParsedSection(heading=current, text=body))
        buf = []

    for raw in text.split("\n"):
        line = raw.strip()
        m = _LATEX_SECTION.match(line)
        if m:
            flush()
            current = m.group(2).strip()  # every \section is a real section
        elif line.startswith("\\title"):
            tm = re.search(r"\\title\*?\{([^}]+)\}", line)
            if tm:
                title = tm.group(1).strip()
        elif line and not line.startswith(("\\begin", "\\end", "\\label", "%")):
            buf.append(line)
        elif not line:
            flush()
    flush()
    return ParsedDoc(source_type="latex", title_hint=title,
                     sections=sections, full_text=text)


def parse_xml(text: str) -> ParsedDoc:
    """Best-effort XML: <sec title=...>/<p> -> sections."""
    import xml.etree.ElementTree as ET

    sections: list[ParsedSection] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ParsedDoc(source_type="xml", full_text=text)
    for sec in root.iter():
        if sec.tag.lower() in ("sec", "section"):
            heading = sec.attrib.get("title", "") or sec.attrib.get("label", "")
            body = re.sub(r"\s+", " ", " ".join(sec.itertext())).strip()
            if heading or body:
                sections.append(ParsedSection(heading=heading, text=body))
    return ParsedDoc(source_type="xml", sections=sections, full_text=text)


def parse_pdf(path) -> ParsedDoc:
    """Extract page text with page anchors via PyMuPDF."""
    import fitz

    doc = fitz.open(str(path))
    pages: dict[int, str] = {}
    for pno in range(1, len(doc) + 1):
        page = doc[pno - 1]
        text = page.get_text("text").strip()
        if text:
            pages[pno] = text
    doc.close()
    marked = "\n\n".join(f"[p{i}]\n{t}" for i, t in sorted(pages.items()))
    title = _pdf_title_hint(pages)
    return ParsedDoc(source_type="pdf", title_hint=title, pages=pages,
                     full_text=marked)


def _pdf_title_hint(pages: dict[int, str]) -> str:
    """First short non-empty line of page 1 is a plausible title hint."""
    first = pages.get(1, "")
    for line in first.split("\n"):
        line = line.strip()
        if 8 <= len(line) <= 90 and not line.endswith((".", ",")):
            return line
    return ""
