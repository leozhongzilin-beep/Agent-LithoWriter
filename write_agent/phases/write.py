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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import prompts, tex
from ..citation import CitationResolver
from ..config import Config
from ..llm import DeepSeekClient, LLMError


@dataclass
class WriteResult:
    section_files: List[str] = field(default_factory=list)
    citation_keys: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def build_paper_context(config: Config, plan: Any, evidence_context: str = "") -> str:
    """Build the paper context block used in every write prompt."""
    claims_str = "\n".join(
        f"- {c.get('claim', '')} (evidence: {c.get('evidence', '')})"
        for c in plan.claims
    )
    context = (
        f"TITLE: {plan.title}\n"
        f"ONE-SENTENCE CONTRIBUTION: {plan.one_sentence_contribution}\n"
        f"FRAMING: {plan.framing}\n"
        f"VENUE: {config.venue} (max {config.max_pages} pages main body)\n"
        f"CLAIMS:\n{claims_str}\n"
        f"KEY WEAKNESSES: {', '.join(plan.weaknesses) if plan.weaknesses else 'none'}\n"
    )
    if evidence_context:
        context += f"\n{evidence_context}\n"
    return context


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
    evidence_context: str = "",
) -> WriteResult:
    """Generate all section LaTeX files from the plan."""
    sections_dir = paper_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    # Clean stale section files
    for f in sections_dir.glob("*.tex"):
        f.unlink()

    paper_context = build_paper_context(config, plan, evidence_context=evidence_context)

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


def _parse_updated_sections(text: str) -> Dict[str, str]:
    """Parse section-file blocks while tolerating common LLM formatting drift."""
    files: Dict[str, str] = {}
    begin_pattern = re.compile(
        r"^\s*=+\s*BEGIN\s+FILE\s*:\s*([^=]+?\.tex)\s*=+\s*$",
        re.IGNORECASE,
    )
    end_pattern = re.compile(
        r"^\s*=+\s*END\s+FILE(?:\s*:\s*([^=]+?\.tex))?\s*=+\s*$",
        re.IGNORECASE,
    )

    def safe_basename(raw: str) -> str | None:
        name = raw.strip().strip("`'").replace("\\", "/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.tex", name):
            return None
        return name

    def strip_code_fence(body: str) -> str:
        lines = body.strip().splitlines()
        if lines and re.fullmatch(r"\s*```[A-Za-z0-9_-]*\s*", lines[0]):
            lines = lines[1:]
        if lines and re.fullmatch(r"\s*```\s*", lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    current: str | None = None
    body_lines: List[str] = []
    for line in text.splitlines():
        if current is None:
            begin_match = begin_pattern.match(line)
            if begin_match:
                current = safe_basename(begin_match.group(1))
                body_lines = []
            continue

        end_match = end_pattern.match(line)
        if end_match:
            end_name = safe_basename(end_match.group(1)) if end_match.group(1) else current
            if end_name == current:
                body = strip_code_fence("\n".join(body_lines))
                if body:
                    files[current] = body
                current = None
                body_lines = []
                continue
        body_lines.append(line)
    return files


def apply_evidence_update(
    client: DeepSeekClient,
    config: Config,
    paper_dir: Path,
    evidence_context: str,
    responses: Iterable[dict[str, Any]] = (),
    requests: Iterable[dict[str, Any]] = (),
) -> List[str]:
    """Update only sections affected by newly completed experiments."""
    paper_text = tex.read_all_sections(paper_dir)
    if not paper_text.strip():
        raise RuntimeError("Cannot apply experiment evidence: no paper sections found")
    system = prompts.SYSTEM_FIXER.format(venue=config.venue, language=config.language)
    user = prompts.UPDATE_FROM_EVIDENCE.format(
        evidence_context=evidence_context,
        paper_text=paper_text,
    )
    sections_dir = paper_dir / "sections"
    existing_files = {path.name for path in sections_dir.glob("*.tex")}

    def recognized_files(text: str) -> Dict[str, str]:
        return {
            filename: body
            for filename, body in _parse_updated_sections(text).items()
            if filename in existing_files
        }

    response_list = list(responses)
    request_list = list(requests)
    anchors: list[tuple[str, float]] = []
    direction_expectations: list[tuple[str, float, float]] = []
    unsupported_claim = False
    for response in response_list:
        request_id = str(response.get("request_id", "response"))
        aggregate_metrics = (
            response.get("aggregate_results", {}).get("metrics", {})
            if isinstance(response.get("aggregate_results"), dict)
            else {}
        )
        if isinstance(aggregate_metrics, dict):
            preferred = ["normalized_score"] + sorted(
                key for key in aggregate_metrics if key != "normalized_score"
            )
            for metric_name in preferred:
                metric = aggregate_metrics.get(metric_name)
                value = metric.get("mean") if isinstance(metric, dict) else None
                if isinstance(value, (int, float)):
                    anchors.append((f"{request_id} {metric_name} mean", float(value)))
                    break
        comparison = response.get("comparison", {})
        if isinstance(comparison, dict):
            for key in (
                "reference_normalized_score_mean",
                "target_normalized_score_mean",
            ):
                value = comparison.get(key)
                if isinstance(value, (int, float)):
                    anchors.append((f"{request_id} {key}", float(value)))
            target = comparison.get("target_normalized_score_mean")
            reference = comparison.get("reference_normalized_score_mean")
            if isinstance(target, (int, float)) and isinstance(reference, (int, float)):
                # This comparison field is defined specifically for a
                # lower-is-better normalized score.
                if "reference_improvement_lower_is_better" in comparison:
                    winner = "reference" if reference < target else "target"
                    direction_expectations.append(
                        (winner, float(target), float(reference))
                    )
            unsupported_claim = unsupported_claim or comparison.get("claim_supported") is False

    # Preserve order while removing duplicate numeric anchors.
    deduped: list[tuple[str, float]] = []
    seen_values: set[float] = set()
    for label, value in anchors:
        rounded = round(value, 10)
        if rounded not in seen_values:
            deduped.append((label, value))
            seen_values.add(rounded)
    anchors = deduped

    def semantic_issues(files: Dict[str, str]) -> list[str]:
        if not response_list:
            return []
        combined = "\n".join(files.values())
        observed = [float(value) for value in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", combined)]
        issues: list[str] = []
        for label, expected in anchors:
            tolerance = max(5e-5, abs(expected) * 5e-4)
            if not any(abs(actual - expected) <= tolerance for actual in observed):
                issues.append(f"missing {label}={expected:.10g}")
        for winner, target, reference in direction_expectations:
            relation_pattern = (
                rf"\b{winner}\b.{{0,180}}\b(?:better|lower)\b|"
                rf"\b(?:better|lower)\b.{{0,180}}\b{winner}\b"
            )
            if not re.search(relation_pattern, combined, flags=re.IGNORECASE | re.DOTALL):
                issues.append(
                    "must state the computed score direction explicitly: "
                    f"under lower-is-better, {winner} mean "
                    f"({reference if winner == 'reference' else target:.10g}) is better/lower "
                    f"than the other mean ({target if winner == 'reference' else reference:.10g})"
                )
        if unsupported_claim and not re.search(
            r"(?:does not support|not supported|insufficient|cannot establish|"
            r"not establish|threshold (?:was|is) not met|不支持|不足以)",
            combined,
            flags=re.IGNORECASE,
        ):
            issues.append("claim_supported=false was not stated as a qualified/retracted claim")
        if unsupported_claim:
            request_text = "\n".join(
                " ".join(
                    str(request.get(key, ""))
                    for key in ("question", "hypothesis", "experiment")
                )
                for request in request_list
            )
            if re.search(r"\bwidth|hidden channels?\b", request_text, flags=re.IGNORECASE):
                stale_width_patterns = (
                    r"confirming that width is (?:the )?(?:dominant|genuine|primary) lever|"
                    r"having established that width helps|"
                    r"width is the dominant (?:architectural )?lever"
                )
                if re.search(stale_width_patterns, combined, flags=re.IGNORECASE):
                    issues.append(
                        "remove stale strong width assertions because claim_supported=false "
                        "(for example, 'width is the dominant lever' or "
                        "'having established that width helps')"
                    )
        for request in request_list:
            origin = request.get("origin", {})
            section_hint = str(origin.get("section", "")) if isinstance(origin, dict) else ""
            required_prefixes = {
                match.group(1) + "_"
                for match in re.finditer(r"Section\s+(\d+)", section_hint, flags=re.IGNORECASE)
            }
            if re.search(r"\bAbstract\b", section_hint, flags=re.IGNORECASE):
                required_prefixes.add("0_")
            if re.search(r"\bIntroduction\b", section_hint, flags=re.IGNORECASE):
                required_prefixes.add("1_")
            for prefix in sorted(required_prefixes):
                matching_existing = [name for name in existing_files if name.startswith(prefix)]
                if matching_existing and not any(name in files for name in matching_existing):
                    issues.append(
                        "request origin requires updating one of: "
                        + ", ".join(sorted(matching_existing))
                    )
        return issues

    attempt = 1

    def save_attempt(text: str, number: int) -> None:
        (paper_dir / f"EVIDENCE_UPDATE_RESPONSE_ATTEMPT_{number}.txt").write_text(
            text,
            encoding="utf-8",
        )

    result = client.chat(system=system, user=user, temperature=0.2, max_tokens=16384)
    save_attempt(result.text, attempt)
    files = recognized_files(result.text)
    if not files:
        attempt += 1
        retry_user = user + prompts.EVIDENCE_UPDATE_FORMAT_RETRY.format(
            filenames=", ".join(sorted(existing_files)),
        )
        retry = client.chat(system=system, user=retry_user, temperature=0.0, max_tokens=16384)
        save_attempt(retry.text, attempt)
        files = recognized_files(retry.text)
    if not files:
        raise RuntimeError(
            "Evidence updater returned no recognized existing section files after format retry; "
            "raw responses were saved in the paper directory"
        )

    issues = semantic_issues(files)
    if issues:
        attempt += 1
        requirements = "\n".join(f"- {issue}" for issue in issues)
        semantic_user = user + prompts.EVIDENCE_UPDATE_SEMANTIC_RETRY.format(
            requirements=requirements,
        )
        retry = client.chat(
            system=system,
            user=semantic_user,
            temperature=0.0,
            max_tokens=16384,
        )
        save_attempt(retry.text, attempt)
        files = recognized_files(retry.text)
        if not files:
            raise RuntimeError(
                "Evidence updater semantic retry returned no recognized section files; "
                "raw responses were saved in the paper directory"
            )
        issues = semantic_issues(files)
        if issues:
            raise RuntimeError(
                "Evidence updater failed semantic evidence checks after retry: "
                + "; ".join(issues)
            )

    changed: List[str] = []
    for filename, body in files.items():
        target = sections_dir / filename
        target.write_text(body.rstrip() + "\n", encoding="utf-8")
        changed.append(filename)
    return changed
