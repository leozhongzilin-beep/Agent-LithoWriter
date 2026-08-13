"""Pipeline orchestration: plan -> write -> review -> finalize.

This is the state machine that runs the full writing agent end-to-end.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import prompts, tex
from .citation import CitationResolver
from .config import Config
from .llm import DeepSeekClient, LLMError
from .phases import plan as plan_phase
from .phases import review as review_phase
from .phases import write as write_phase


class Pipeline:
    def __init__(self, config: Config, verbose: bool = True, client: Optional[DeepSeekClient] = None):
        self.config = config
        self.verbose = verbose
        self.client = client or DeepSeekClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        self.citation_resolver = CitationResolver(config) if config.dblp_verify else None

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    # ------------------------------------------------------------------
    def run(
        self,
        source: str,
        paper_dir: Optional[Path] = None,
        max_rounds: Optional[int] = None,
        skip_review: bool = False,
    ) -> Dict[str, Any]:
        """Run the full pipeline.

        Args:
            source: research topic / brief / narrative report path.
            paper_dir: output directory (default: <output_dir>/paper).
            max_rounds: override review loop rounds.
            skip_review: skip the review loop (draft-only mode).
        """
        paper_dir = paper_dir or (self.config.output_dir / "paper")
        paper_dir.mkdir(parents=True, exist_ok=True)
        tex.create_project_structure(paper_dir, paper_dir / "sections")

        report: Dict[str, Any] = {
            "input": source,
            "venue": self.config.venue,
            "date": datetime.now(timezone.utc).isoformat(),
            "model": self.config.model_name,
        }

        # ---- Phase 1: Plan ----
        self.log("\n" + "=" * 70)
        self.log("PHASE 1/4: Planning")
        self.log("=" * 70)
        try:
            plan = plan_phase.run_plan(self.client, self.config, source, paper_dir)
        except (RuntimeError, LLMError) as e:
            report["error"] = f"Planning failed: {e}"
            return report
        self.log(f"[plan] Title: {plan.title}")
        self.log(f"[plan] One-sentence contribution: {plan.one_sentence_contribution}")
        self.log(f"[plan] Sections: {len(plan.sections)} "
                 f"({', '.join(s['title'] for s in plan.sections)})")
        report["plan"] = {
            "title": plan.title,
            "one_sentence_contribution": plan.one_sentence_contribution,
            "num_sections": len(plan.sections),
        }

        # ---- Phase 2: Write ----
        self.log("\n" + "=" * 70)
        self.log("PHASE 2/4: Writing LaTeX")
        self.log("=" * 70)
        try:
            write_result = write_phase.run_write(
                self.client, self.config, plan, paper_dir, self.citation_resolver
            )
        except (RuntimeError, LLMError) as e:
            report["error"] = f"Writing failed: {e}"
            return report
        self.log(f"[write] Sections written: {len(write_result.section_files)}")
        if write_result.citation_keys:
            self.log(f"[write] Resolved citations: {len(write_result.citation_keys)} keys")
        for w in write_result.warnings:
            self.log(f"[write] WARNING: {w}")
        report["write"] = {
            "num_sections": len(write_result.section_files),
            "num_citations": len(write_result.citation_keys),
            "warnings": write_result.warnings,
        }

        # ---- Phase 3: Review loop ----
        if skip_review:
            self.log("\n[review] SKIPPED (--skip-review)")
            report["review"] = {"skipped": True}
            review_result = None
        else:
            self.log("\n" + "=" * 70)
            self.log(f"PHASE 3/4: Review loop (up to {max_rounds or self.config.review_max_rounds} rounds)")
            self.log("=" * 70)
            try:
                review_result = review_phase.run_review_loop(
                    self.client, self.config, paper_dir, max_rounds=max_rounds
                )
            except (RuntimeError, LLMError) as e:
                report["error"] = f"Review loop failed: {e}"
                return report
            self.log(f"[review] Final: {review_result.final_score}/10 "
                     f"({review_result.final_verdict}) after {review_result.round_count} rounds "
                     f"[{review_result.stopped_reason}]")
            report["review"] = {
                "rounds": review_result.round_count,
                "final_score": review_result.final_score,
                "final_verdict": review_result.final_verdict,
                "stopped_reason": review_result.stopped_reason,
            }

        # ---- Phase 4: Finalize ----
        self.log("\n" + "=" * 70)
        self.log("PHASE 4/4: Finalize")
        self.log("=" * 70)
        finalize = self._finalize(paper_dir)
        report["finalize"] = finalize

        # Write pipeline report
        report_path = paper_dir / "PIPELINE_REPORT.md"
        report_path.write_text(self._render_report(report), encoding="utf-8")
        self.log(f"[finalize] Report: {report_path}")

        # Write machine-readable report
        (paper_dir / "PIPELINE_REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.log("\n" + "=" * 70)
        self.log("PIPELINE COMPLETE")
        self.log(f"Output directory: {paper_dir}")
        self.log("=" * 70)
        return report

    # ------------------------------------------------------------------
    def _finalize(self, paper_dir: Path) -> Dict[str, Any]:
        """Run the final quality audit (5 passes) + consistency checks.

        Uses the LLM for the 5-pass audit, plus deterministic checks for
        duplicate labels and citation keys without bib entries.
        """
        paper_text = tex.read_all_sections(paper_dir)
        result: Dict[str, Any] = {}

        # Deterministic: duplicate labels
        dups = tex.ensure_labels_unique(paper_text)
        result["duplicate_labels"] = dups

        # Deterministic: cited keys vs references.bib
        cited = tex.extract_citation_keys(paper_text)
        bib_path = paper_dir / "references.bib"
        bib_keys: List[str] = []
        if bib_path.exists():
            bib_text = bib_path.read_text(encoding="utf-8", errors="ignore")
            from .citation import extract_cited_keys
            bib_keys = extract_cited_keys(bib_text)
        uncited_in_bib = sorted(set(cited) - set(bib_keys))
        result["cited_without_bib"] = uncited_in_bib

        # LLM 5-pass audit
        try:
            system = prompts.SYSTEM_FINALIZER.format(language=self.config.language)
            user = prompts.FINAL_AUDIT.format(paper_text=paper_text)
            audit = self.client.chat_json(system=system, user=user, temperature=0.2, max_tokens=4096)
            result["audit"] = audit
            passes = audit.get("passes_clean", [])
            result["passes_clean_count"] = sum(1 for p in passes if p)
        except LLMError as e:
            result["audit_error"] = str(e)
            result["passes_clean_count"] = 0

        return result

    # ------------------------------------------------------------------
    def _render_report(self, report: Dict[str, Any]) -> str:
        try:
            plan = report.get("plan", {})
            review = report.get("review", {})
            finalize = report.get("finalize", {})
            audit = finalize.get("audit", {})
            passes = audit.get("passes_clean", [])
            score_table = ""
            if "rounds" in review:
                for r in range(1, int(review.get("rounds", 0)) + 1):
                    score_table += f"| {r} | {review.get('final_score', '?')} | {review.get('final_verdict', '?')} | (see PAPER_REVIEW_LOG.md) |\n"
            remaining = finalize.get("cited_without_bib", [])
            remaining_lines = (
                "\n".join(f"- Citation {c} has no bibliography entry" for c in remaining)
                if remaining else "- None"
            )
            return prompts.FINAL_REPORT_TEMPLATE.format(
                input_desc=report.get("input", ""),
                venue=report.get("venue", ""),
                date=report.get("date", ""),
                model=report.get("model", ""),
                plan_status="done" if plan else "failed",
                write_status="done" if report.get("write") else "failed",
                review_status="skipped" if report.get("review", {}).get("skipped") else ("done" if "rounds" in review else "failed"),
                rounds=review.get("rounds", 0),
                score=review.get("final_score", "?"),
                finalize_status="done",
                score_table=score_table,
                passes_clean=(", ".join(str(p) for p in passes)) if passes else "n/a",
                audit_overall=audit.get("overall", "n/a"),
                remaining_issues=remaining_lines,
            )
        except Exception:
            return json.dumps(report, ensure_ascii=False, indent=2)
