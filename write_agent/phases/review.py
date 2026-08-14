"""Phase 3: Review loop -- autonomous review -> fix -> re-review.

Design principles:
    - reviewer independence: every round uses a fresh zero-context prompt
      (zero-context reviewer independence). The reviewer never sees
      "what we changed" --
      only the current paper text. This prevents score inflation.
    - stop condition: score >= min_score AND verdict in acceptable_verdicts
    - state persistence: REVIEW_STATE.json allows crash recovery
    - human checkpoint: optional pause after each review
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import prompts, tex
from ..config import Config
from ..llm import DeepSeekClient, LLMError


@dataclass
class ReviewRound:
    round: int
    score: float
    verdict: str
    summary: str
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[Dict[str, str]] = field(default_factory=list)
    raw_response: str = ""


@dataclass
class ReviewResult:
    rounds: List[ReviewRound] = field(default_factory=list)
    final_score: float = 0.0
    final_verdict: str = "not ready"
    stopped_reason: str = ""
    round_count: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewLoopState:
    """Crash-recoverable loop state."""

    def __init__(self, paper_dir: Path):
        self.path = paper_dir / "REVIEW_STATE.json"

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, state: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def complete(self, final: Dict[str, Any]) -> None:
        final["status"] = "completed"
        final["timestamp"] = _now_iso()
        self.save(final)


def _parse_review_json(text: str) -> Dict[str, Any]:
    """Parse a review JSON response, with fallbacks for common formats."""
    from ..llm import extract_json

    obj = extract_json(text)
    if obj is None:
        raise LLMError(f"Reviewer returned non-JSON response: {text[:300]}")
    # Normalize score/verdict types
    try:
        obj["score"] = float(obj.get("score", 0))
    except (TypeError, ValueError):
        obj["score"] = 0.0
    verdict = str(obj.get("verdict", "not ready")).strip().lower()
    if "ready" in verdict:
        obj["verdict"] = "ready"
    elif "almost" in verdict or "not quite" in verdict:
        obj["verdict"] = "almost"
    else:
        obj["verdict"] = "not ready"
    weaknesses = obj.get("weaknesses", [])
    if isinstance(weaknesses, list) and weaknesses and isinstance(weaknesses[0], str):
        obj["weaknesses"] = [
            {"severity": "MAJOR", "issue": w, "fix": "", "location": ""}
            for w in weaknesses
        ]
    return obj


def _run_review(client: DeepSeekClient, config: Config, paper_text: str, round_num: int) -> Dict[str, Any]:
    """Run a single zero-context review round."""
    system = prompts.SYSTEM_REVIEWER.format(venue=config.venue, language=config.language)
    if round_num <= 1:
        user = prompts.REVIEW_ROUND1.format(venue=config.venue, paper_text=paper_text)
    else:
        user = prompts.REVIEW_ROUND2.format(venue=config.venue, paper_text=paper_text)
    result = client.chat(system=system, user=user, temperature=0.2, max_tokens=8192)
    return _parse_review_json(result.text)


def _parse_fixed_sections(fix_text: str) -> Dict[str, str]:
    """Parse the fixer's output into {filename: latex_body}.

    Expected format:
        ===== BEGIN FILE: 1_introduction.tex =====
        ...
        ===== END FILE: 1_introduction.tex =====
    """
    files: Dict[str, str] = {}
    pattern = re.compile(
        r"===== BEGIN FILE:\s*([^\s=]+)\.tex\s*=====(.*?)===== END FILE:\s*\1\.tex\s*=====",
        re.DOTALL,
    )
    for m in pattern.finditer(fix_text):
        files[m.group(1) + ".tex"] = m.group(2).strip()
    # Fallback: try to split by BEGIN FILE markers if strict regex failed
    if not files:
        parts = re.split(r"=====\s*BEGIN FILE:\s*([^\s=]+\.tex)\s*=====", fix_text)
        if len(parts) >= 3:
            for i in range(1, len(parts) - 1, 2):
                files[parts[i]] = re.sub(r"===== END FILE:.*$", "", parts[i + 1], flags=re.DOTALL).strip()
    return files


def _apply_fixes(
    client: DeepSeekClient,
    config: Config,
    paper_dir: Path,
    paper_text: str,
    review: Dict[str, Any],
) -> List[str]:
    """Apply reviewer feedback via the fixer model. Returns list of changed files."""
    system = prompts.SYSTEM_FIXER.format(venue=config.venue, language=config.language)
    review_json = json.dumps(review, ensure_ascii=False, indent=2)
    user = prompts.FIX_PROMPT.format(review_json=review_json, paper_text=paper_text)
    result = client.chat(system=system, user=user, temperature=0.3, max_tokens=16384)

    files = _parse_fixed_sections(result.text)
    if not files:
        # If the fixer failed to produce the delimited format, don't risk
        # destroying sections. Log and return empty.
        return []

    changed = []
    sections_dir = paper_dir / "sections"
    for fname, body in files.items():
        target = sections_dir / fname
        if not target.exists():
            # Only allow overwriting existing section files
            continue
        target.write_text(body + "\n", encoding="utf-8")
        changed.append(fname)
    return changed


def _write_review_log(paper_dir: Path, rounds: List[ReviewRound]) -> None:
    """Write/append the cumulative review log PAPER_REVIEW_LOG.md."""
    log_path = paper_dir / "PAPER_REVIEW_LOG.md"
    lines = ["# Paper Review Log", ""]
    for r in rounds:
        lines += [
            f"## Round {r.round} ({_now_iso()})",
            "",
            f"- **Score**: {r.score}/10",
            f"- **Verdict**: {r.verdict}",
            f"- **Summary**: {r.summary}",
            "",
            "### Strengths",
            "",
        ]
        lines += [f"- {s}" for s in r.strengths]
        lines += ["", "### Weaknesses", ""]
        for w in r.weaknesses:
            lines.append(f"- **[{w.get('severity', '')}]** {w.get('issue', '')} (loc: {w.get('location', '')})")
            if w.get("fix"):
                lines.append(f"  - Fix: {w['fix']}")
        lines += ["", "### Reviewer Raw Response", "", "<details>", "<summary>Click to expand</summary>", "", r.raw_response, "", "</details>", ""]
    log_path.write_text("\n".join(lines), encoding="utf-8")


def run_review_loop(
    client: DeepSeekClient,
    config: Config,
    paper_dir: Path,
    max_rounds: Optional[int] = None,
) -> ReviewResult:
    """Run the autonomous review -> fix -> re-review loop."""
    max_rounds = max_rounds or config.review_max_rounds
    min_score = config.review_min_score
    acceptable = set(config.acceptable_verdicts)
    human_checkpoint = config.human_checkpoint

    state_file = ReviewLoopState(paper_dir)

    # Recovery check
    saved = state_file.load()
    start_round = 1
    rounds: List[ReviewRound] = []
    if saved and saved.get("status") == "in_progress":
        saved_ts = saved.get("timestamp", "")
        try:
            saved_dt = datetime.fromisoformat(saved_ts)
            age_hours = (datetime.now(timezone.utc) - saved_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = 999
        if age_hours <= 24:
            start_round = int(saved.get("round", 1)) + 1
            # restore prior rounds from log if available
            rounds = _load_rounds_from_log(paper_dir)
            print(f"[review] Recovered from REVIEW_STATE.json, resuming at round {start_round}")
        else:
            state_file.complete({"status": "completed"})
            rounds = []

    result = ReviewResult()

    for rnd in range(start_round, max_rounds + 1):
        print(f"\n[review] Round {rnd}/{max_rounds}")

        # 1. Collect current paper text
        paper_text = tex.read_all_sections(paper_dir)
        if not paper_text.strip():
            print("[review] No sections found; aborting review loop.")
            result.stopped_reason = "no_sections"
            break

        # 2. Review (zero-context, fresh)
        review_data = _run_review(client, config, paper_text, rnd)
        raw = json.dumps(review_data, ensure_ascii=False, indent=2)

        round_rec = ReviewRound(
            round=rnd,
            score=float(review_data.get("score", 0)),
            verdict=str(review_data.get("verdict", "not ready")),
            summary=str(review_data.get("summary", "")),
            strengths=review_data.get("strengths", []),
            weaknesses=review_data.get("weaknesses", []),
            raw_response=raw,
        )
        rounds.append(round_rec)
        _write_review_log(paper_dir, rounds)

        print(f"[review] Round {rnd}: score={round_rec.score}/10 verdict={round_rec.verdict}")

        # 3. Persist state
        state_file.save({
            "round": rnd,
            "score": round_rec.score,
            "verdict": round_rec.verdict,
            "status": "in_progress",
            "timestamp": _now_iso(),
        })

        # 4. Human checkpoint
        if human_checkpoint:
            _human_checkpoint(round_rec)

        # 5. Stop condition
        if round_rec.score >= min_score and round_rec.verdict in acceptable:
            result.rounds = rounds
            result.final_score = round_rec.score
            result.final_verdict = round_rec.verdict
            result.stopped_reason = "positive_assessment"
            result.round_count = rnd
            state_file.complete({"status": "completed", "round": rnd})
            print(f"[review] Positive assessment reached: {round_rec.score}/10 ({round_rec.verdict})")
            return result

        # 6. Apply fixes (only for CRITICAL/MAJOR, skip MINOR unless cheap)
        critical = [w for w in round_rec.weaknesses if w.get("severity", "").upper() == "CRITICAL"]
        major = [w for w in round_rec.weaknesses if w.get("severity", "").upper() == "MAJOR"]
        minor = [w for w in round_rec.weaknesses if w.get("severity", "").upper() == "MINOR"]

        if not critical and not major:
            print(f"[review] No CRITICAL/MAJOR issues; only {len(minor)} minor. Moving on.")
            # If no major issues but not yet passing, continue to next round (the
            # reviewer may re-score higher with same text; also handles partial fixes)
            if rnd >= max_rounds:
                break
            continue

        fix_targets = critical + major + minor[:2]
        review_for_fix = dict(review_data)
        review_for_fix["weaknesses"] = fix_targets

        print(f"[review] Applying fixes for {len(fix_targets)} issues "
              f"({len(critical)} CRITICAL, {len(major)} MAJOR, {len(minor[:2])} MINOR)...")

        changed = _apply_fixes(client, config, paper_dir, paper_text, review_for_fix)
        if changed:
            print(f"[review] Fixed files: {', '.join(changed)}")
        else:
            print("[review] Fixer produced no recognized file deltas; continuing (paper unchanged).")

    # Max rounds exhausted without positive assessment
    result.rounds = rounds
    if rounds:
        result.final_score = rounds[-1].score
        result.final_verdict = rounds[-1].verdict
    result.stopped_reason = "max_rounds"
    result.round_count = len(rounds)
    state_file.complete({"status": "completed", "round": len(rounds)})
    return result


def _load_rounds_from_log(paper_dir: Path) -> List[ReviewRound]:
    """Best-effort reload of prior rounds from PAPER_REVIEW_LOG.md."""
    log_path = paper_dir / "PAPER_REVIEW_LOG.md"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    rounds = []
    # Simple parse: each "## Round N" section
    blocks = re.split(r"^## Round ", text, flags=re.MULTILINE)
    for block in blocks[1:]:
        try:
            num = int(re.match(r"(\d+)", block).group(1))
        except (AttributeError, ValueError):
            continue
        score_m = re.search(r"- \*\*Score\*\*: ([\d.]+)/10", block)
        verdict_m = re.search(r"- \*\*Verdict\*\*: (\w+)", block)
        summary_m = re.search(r"- \*\*Summary\*\*: (.+)", block)
        rounds.append(ReviewRound(
            round=num,
            score=float(score_m.group(1)) if score_m else 0.0,
            verdict=verdict_m.group(1) if verdict_m else "not ready",
            summary=summary_m.group(1) if summary_m else "",
            raw_response="",
        ))
    return rounds


def _human_checkpoint(round_rec: ReviewRound) -> None:
    """Pause and ask the user whether to proceed with fixes."""
    print("\n" + "=" * 60)
    print(f"Round {round_rec.round} review complete.")
    print(f"Score: {round_rec.score}/10 -- {round_rec.verdict}")
    print("Top weaknesses:")
    for w in round_rec.weaknesses[:5]:
        print(f"  - [{w.get('severity', '')}] {w.get('issue', '')}")
    print("\nOptions: [enter]=apply fixes, 'skip'=skip, 'stop'=end loop")
    choice = input("> ").strip().lower()
    if choice == "stop":
        raise KeyboardInterrupt("User stopped the review loop at checkpoint.")
