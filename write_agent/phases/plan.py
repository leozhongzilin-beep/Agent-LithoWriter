"""Phase 1: Planning -- parse input material into a paper outline.

Input:  research topic / brief / narrative report / experiment data
Output: PAPER_PLAN.md with claims-evidence matrix, section structure,
        figure plan, citation scaffolding.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config
from ..llm import DeepSeekClient, LLMError
from .. import prompts


@dataclass
class PlanResult:
    one_sentence_contribution: str = ""
    title: str = ""
    claims: List[Dict[str, Any]] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    framing: str = ""
    paper_type: str = "empirical"
    sections: List[Dict[str, Any]] = field(default_factory=list)
    figure_plan: List[Dict[str, Any]] = field(default_factory=list)
    citation_plan: Dict[str, Any] = field(default_factory=dict)
    input_description: str = ""

    @property
    def section_filenames(self) -> List[str]:
        return [s.get("filename", f"{s.get('id', '')}_{s.get('title', 'section').lower()}.tex")
                for s in self.sections]


def load_input_text(source: str) -> str:
    """Load input material from a file path or treat as inline text.

    If `source` is an existing file, read it. Otherwise treat as inline brief.
    """
    p = Path(source)
    if p.exists():
        text = p.read_text(encoding="utf-8", errors="ignore")
        return text
    return source


def extract_claims(client: DeepSeekClient, config: Config, input_text: str) -> Dict[str, Any]:
    """Extract claims-evidence matrix + framing from the input material."""
    system = prompts.SYSTEM_PLANNER.format(language=config.language, venue=config.venue, max_pages=config.max_pages)
    user = prompts.PLAN_EXTRACT_CLAIMS.format(input_text=input_text)
    try:
        return client.chat_json(system=system, user=user, temperature=0.2, max_tokens=4096)
    except LLMError as e:
        raise RuntimeError(f"Claim extraction failed: {e}")


def build_outline(client: DeepSeekClient, config: Config, claims_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the section-by-section outline from the claims matrix."""
    claims_matrix = "\n".join(
        f"- [{c.get('status', '?')}] {c.get('claim', '')} -- evidence: {c.get('evidence', '')}"
        for c in claims_data.get("claims", [])
    )
    system = prompts.SYSTEM_PLANNER.format(language=config.language, venue=config.venue, max_pages=config.max_pages)
    user = prompts.PLAN_BUILD_OUTLINE.format(
        contribution=claims_data.get("one_sentence_contribution", ""),
        title=claims_data.get("title", ""),
        claims_matrix=claims_matrix,
        weaknesses=", ".join(claims_data.get("key_weaknesses", [])),
        framing=claims_data.get("suggested_framing", ""),
        venue=config.venue,
        max_pages=config.max_pages,
        min_sections=config.get("plan", "min_sections", default=5),
        max_sections=config.get("plan", "max_sections", default=8),
    )
    try:
        return client.chat_json(system=system, user=user, temperature=0.3, max_tokens=8192)
    except LLMError as e:
        raise RuntimeError(f"Outline generation failed: {e}")


def sanitize_section_filename(name: str) -> str:
    """Ensure a filename ends with .tex and has no path separators."""
    name = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    if not name.endswith(".tex"):
        name += ".tex"
    return name


def normalize_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize section dicts, ensuring abstract is section 0 and filenames are safe."""
    result = []
    seen_ids = set()
    for s in sections:
        s = dict(s)
        sid = str(s.get("id", str(len(result) + 1)))
        # Ensure abstract is id 0
        title = str(s.get("title", "Section"))
        is_abstract = sid == "0" or "abstract" in title.lower()
        if is_abstract:
            sid = "0"
        if sid in seen_ids:
            # de-dup ids
            sid = f"{sid}_{len(result)}"
        seen_ids.add(sid)
        s["id"] = sid
        s["title"] = title
        fname = sanitize_section_filename(s.get("filename", f"{sid}_{title.lower().replace(' ', '_')}.tex"))
        # force abstract filename
        if is_abstract:
            fname = "0_abstract.tex"
        s["filename"] = fname
        s.setdefault("target_pages", 0.5)
        s.setdefault("key_points", [])
        s.setdefault("claims", [])
        s.setdefault("citations_hint", [])
        s.setdefault("figures", [])
        result.append(s)
    # Ensure abstract is first
    result.sort(key=lambda s: 0 if s["id"] == "0" else 1)
    return result


def write_plan_md(result: PlanResult, output_path: Path) -> None:
    """Write the human-readable PAPER_PLAN.md."""
    lines = [
        "# Paper Plan",
        "",
        f"**Title**: {result.title}",
        "",
        f"**One-sentence contribution**: {result.one_sentence_contribution}",
        "",
        f"**Venue**: {result.title}  **Type**: {result.paper_type}",
        "",
        "## Claims-Evidence Matrix",
        "",
        "| Claim | Evidence | Status | Section |",
        "|-------|----------|--------|---------|",
    ]
    for c in result.claims:
        lines.append(f"| {c.get('claim', '')} | {c.get('evidence', '')} | {c.get('status', '')} | {c.get('section', '')} |")
    lines += ["", "## Structure", ""]
    for s in result.sections:
        lines.append(f"### §{s['id']} {s['title']}  ({s.get('target_pages', 0.5)} pages)")
        for kp in s.get("key_points", []):
            lines.append(f"- {kp}")
        if s.get("figures"):
            lines.append(f"- **Figures**: {', '.join(s['figures'])}")
    lines += ["", "## Figure Plan", ""]
    lines.append("| ID | Type | Description | Data Source |")
    lines.append("|----|------|-------------|-------------|")
    for f in result.figure_plan:
        lines.append(f"| {f.get('id', '')} | {f.get('type', '')} | {f.get('description', '')} | {f.get('data_source', '')} |")
    lines += ["", "## Citation Plan", ""]
    for k, v in result.citation_plan.items():
        lines.append(f"- **{k}**: {', '.join(v) if isinstance(v, list) else v}")
    lines += ["", "## Weaknesses & Risks", ""]
    for w in result.weaknesses:
        lines.append(f"- {w}")
    lines += ["", "## Next Steps", "", "- [ ] Write sections (Phase 2)", "- [ ] Review loop (Phase 3)"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def run_plan(
    client: DeepSeekClient,
    config: Config,
    source: str,
    paper_dir: Path,
) -> PlanResult:
    """Run Phase 1 planning and return a PlanResult."""
    input_text = load_input_text(source)

    claims_data = extract_claims(client, config, input_text)
    outline_data = build_outline(client, config, claims_data)

    result = PlanResult(
        one_sentence_contribution=claims_data.get("one_sentence_contribution", ""),
        title=claims_data.get("title", "Untitled Paper"),
        claims=claims_data.get("claims", []),
        weaknesses=claims_data.get("key_weaknesses", []),
        framing=claims_data.get("suggested_framing", ""),
        paper_type=outline_data.get("paper_type", "empirical"),
        sections=normalize_sections(outline_data.get("sections", [])),
        figure_plan=outline_data.get("figure_plan", []),
        citation_plan=outline_data.get("citation_plan", {}),
        input_description=source,
    )

    # Write PAPER_PLAN.md
    plan_path = paper_dir / "PAPER_PLAN.md"
    write_plan_md(result, plan_path)

    # Also persist machine-readable plan for downstream phases
    plan_json = paper_dir / "paper_plan.json"
    plan_json.write_text(
        json.dumps(
            {
                "title": result.title,
                "one_sentence_contribution": result.one_sentence_contribution,
                "paper_type": result.paper_type,
                "sections": result.sections,
                "figure_plan": result.figure_plan,
                "citation_plan": result.citation_plan,
                "claims": result.claims,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result
