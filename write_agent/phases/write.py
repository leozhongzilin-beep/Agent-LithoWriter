"""Phase 2: Writing -- generate LaTeX section by section.

Follows a paper-write methodology:
    - abstract: five-sentence formula
    - intro: hook -> gap -> approach -> bullets -> preview
    - related work: >= 1 page, organized by category
    - method: notation, formal statements, proof sketches
    - experiments: setup -> main results -> ablations
    - conclusion: rephrased contributions, honest limitations

Each section is written in sequence, with previously written sections fed
back as context for terminology consistency (the Banana Rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import prompts, tex
from ..citation import CitationResolver
from ..config import Config
from ..llm import DeepSeekClient, LLMError


@dataclass
class WriteResult:
    section_files: List[str] = field(default_factory=list)
    citation_keys: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def build_paper_context(config: Config, plan: Any) -> str:
    """Build the paper context block used in every write prompt."""
    claims_str = "\n".join(
        f"- {c.get('claim', '')} (evidence: {c.get('evidence', '')})"
        for c in plan.claims
    )
    return (
        f"TITLE: {plan.title}\n"
        f"ONE-SENTENCE CONTRIBUTION: {plan.one_sentence_contribution}\n"
        f"FRAMING: {plan.framing}\n"
        f"VENUE: {config.venue} (max {config.max_pages} pages main body)\n"
        f"CLAIMS:\n{claims_str}\n"
        f"KEY WEAKNESSES: {', '.join(plan.weaknesses) if plan.weaknesses else 'none'}\n"
    )


def build_section_spec(section: Dict[str, Any]) -> str:
    """Render a section dict into a spec block for the prompt."""
    lines = [
        f"Section {section.get('id', '?')}: {section.get('title', '')}",
        f"Purpose: {section.get('purpose', '')}",
        f"Target length: {section.get('target_pages', 0.5)} page(s)",
    ]
    kps = section.get("key_points", [])
    if kps:
        lines.append("Key points to cover:")
        lines.extend(f"  - {k}" for k in kps)
    figs = section.get("figures", [])
    if figs:
        lines.append(f"Figures/tables: {', '.join(figs)}")
    return "\n".join(lines)


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


def _write_abstract(
    client: DeepSeekClient,
    config: Config,
    paper_context: str,
) -> str:
    system = prompts.SYSTEM_WRITER.format(venue=config.venue, language=config.language)
    user = prompts.WRITE_ABSTRACT.format(
        paper_context=paper_context,
        min_words=config.get("write", "abstract_words_min", default=150),
        max_words=config.get("write", "abstract_words_max", default=250),
    )
    result = client.chat(system=system, user=user, temperature=0.6)
    return result.text.strip().strip('"').strip()


def _write_generic_section(
    client: DeepSeekClient,
    config: Config,
    paper_context: str,
    section: Dict[str, Any],
    written_so_far: str,
    citation_keys: List[str],
    kb_cards: str = "",
) -> str:
    title = section.get("title", "").lower()
    system = prompts.SYSTEM_WRITER.format(venue=config.venue, language=config.language)
    section_spec = build_section_spec(section)

    if "introduction" in title:
        template = prompts.WRITE_SECTION_SPECIFIC_INTRO
    elif "related" in title:
        template = prompts.WRITE_SECTION_SPECIFIC_RELATED
    elif any(k in title for k in ("method", "model", "approach", "preliminaries", "setup", "formulation")):
        template = prompts.WRITE_SECTION_SPECIFIC_METHOD
    else:
        template = prompts.WRITE_SECTION

    if template is prompts.WRITE_SECTION_SPECIFIC_INTRO:
        user = template.format(
            paper_context=paper_context,
            section_spec=section_spec,
            written_so_far=written_so_far,
            target_pages=section.get("target_pages", 1.0),
            num_bullets=config.get("plan", "num_contribution_bullets", default=4),
        )
    elif template is prompts.WRITE_SECTION_SPECIFIC_RELATED:
        keys_str = ", ".join(citation_keys) if citation_keys else "NONE AVAILABLE"
        user = template.format(
            paper_context=paper_context,
            section_spec=section_spec,
            written_so_far=written_so_far,
            target_pages=section.get("target_pages", 1.0),
            citation_keys=keys_str,
            kb_cards=kb_cards or "NONE",
        )
    else:
        keys_str = ", ".join(citation_keys) if citation_keys else "NONE AVAILABLE"
        user = template.format(
            paper_context=paper_context,
            section_spec=section_spec,
            written_so_far=written_so_far,
            target_pages=section.get("target_pages", 1.0),
            citation_keys=keys_str,
        )

    result = client.chat(system=system, user=user, temperature=0.6, max_tokens=8192)
    return result.text.strip()


def run_write(
    client: DeepSeekClient,
    config: Config,
    plan: Any,
    paper_dir: Path,
    citation_resolver: Optional[CitationResolver] = None,
) -> WriteResult:
    """Generate all section LaTeX files from the plan."""
    sections_dir = paper_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    # Clean stale section files
    for f in sections_dir.glob("*.tex"):
        f.unlink()

    paper_context = build_paper_context(config, plan)

    # Pre-resolve citation hints into real bib entries.
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
        # de-dup hints preserving order
        for h in hint_list:
            if h and h not in seen_hints:
                seen_hints.add(h)
                entry = citation_resolver.resolve_query(h)
                if entry and entry.verified:
                    resolved_entries.append(entry)
                    citation_keys.append(entry.key)

    written_files: List[str] = []
    written_so_far = ""
    warnings: list[str] = []

    for section in plan.sections:
        fname = section["filename"]
        sid = section["id"]
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
        if sid == "0":
            body = _write_abstract(client, config, paper_context)
        else:
            body = _write_generic_section(
                client, config, paper_context, section, written_so_far,
                citation_keys, kb_cards=kb_cards,
            )
        # Deterministic enforcement: drop any cite key the resolver did not
        # provide (root cause B). The LLM prompt asks for this, but the LLM
        # still fabricates keys, so we strip them here.
        body, dropped_keys = tex.sanitize_citations(body, set(citation_keys))
        if dropped_keys:
            warnings.append(
                f"Section {sid}: dropped fabricated citation keys "
                f"{', '.join(dropped_keys)}"
            )
        # wrap with section command
        full_tex = tex.wrap_section(fname, body)
        (sections_dir / fname).write_text(full_tex, encoding="utf-8")
        written_files.append(fname)
        written_so_far += f"\n% === {fname} ===\n{full_tex}\n"

    # Write math_commands.tex
    (paper_dir / "math_commands.tex").write_text(tex.build_math_commands(), encoding="utf-8")

    # Write main.tex
    main_tex = tex.build_main_tex(
        title=plan.title,
        venue=config.venue,
        section_filenames=written_files,
        anonymous=config.anonymous,
        max_pages_hint=config.max_pages,
    )
    (paper_dir / "main.tex").write_text(main_tex, encoding="utf-8")

    # Write references.bib from resolved entries (only cited ones)
    if resolved_entries:
        from ..citation import write_bibliography
        write_bibliography(resolved_entries, paper_dir / "references.bib")

    if citation_resolver is not None:
        unresolved = [e for e in citation_resolver._cache.values() if not e.verified]
        if unresolved:
            warnings.append(
                f"{len(unresolved)} citation hints could not be verified: "
                f"{', '.join(e.title for e in unresolved[:5])}"
            )

    return WriteResult(section_files=written_files, citation_keys=citation_keys, warnings=warnings)
