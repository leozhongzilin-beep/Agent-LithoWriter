"""Command-line interface for the writing agent.

Usage:
    python -m write_agent.cli --topic "Your research topic here"
    python -m write_agent.cli --narrative NARRATIVE_REPORT.md
    python -m write_agent.cli --topic "..." --venue NeurIPS --max-rounds 4
    python -m write_agent.cli --topic "..." --skip-review

Use a `.env` file next to config.yaml, or export DEEPSEEK_API_KEY.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .config import load_config
from .pipeline import Pipeline
from .llm import LLMError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="writing-agent",
        description="Autonomous academic paper writing agent (DeepSeek-powered).",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--topic", help="Research topic or brief (inline text).")
    src.add_argument("--narrative", help="Path to a narrative report / experiment data file.")
    src.add_argument("--input", help="Alias for --narrative.")

    p.add_argument("--venue", default=None,
                   help="Target venue: ICLR, NeurIPS, ICML, CVPR, ACL, AAAI, IEEE_JOURNAL, IEEE_CONF.")
    p.add_argument("--max-pages", type=int, default=None, help="Main body page limit.")
    p.add_argument("--max-rounds", type=int, default=None, help="Review loop max rounds (default 3).")
    p.add_argument("--skip-review", action="store_true", help="Skip the review loop (draft only).")
    p.add_argument("--output-dir", default=None, help="Output directory (default: ./output).")
    p.add_argument("--model", default=None, help="DeepSeek model override (deepseek-chat / deepseek-reasoner).")
    p.add_argument("--config", default=None, help="Path to config.yaml override.")
    p.add_argument("--min-score", type=float, default=None, help="Review stop threshold (default 6.0).")
    p.add_argument("--human-checkpoint", action="store_true", help="Pause after each review round.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Load config; CLI overrides
    cfg = load_config(yaml_path=Path(args.config) if args.config else None)
    if args.venue:
        cfg.data.setdefault("pipeline", {})["venue"] = args.venue
    if args.max_pages:
        cfg.data.setdefault("pipeline", {})["max_pages"] = args.max_pages
    if args.max_rounds:
        cfg.data.setdefault("review", {})["max_rounds"] = args.max_rounds
    if args.min_score:
        cfg.data.setdefault("review", {})["min_score"] = args.min_score
    if args.output_dir:
        cfg.data.setdefault("pipeline", {})["output_dir"] = args.output_dir
    if args.model:
        cfg.data.setdefault("model", {})["name"] = args.model
    if args.human_checkpoint:
        cfg.data.setdefault("review", {})["human_checkpoint"] = True

    source = args.topic or args.narrative or args.input
    if not source:
        print("Error: provide --topic or --narrative", file=sys.stderr)
        return 2

    print(f"Writing agent starting...")
    print(f"  Input: {source}")
    print(f"  Venue: {cfg.venue} | Max pages: {cfg.max_pages} | Model: {cfg.model_name}")
    print(f"  Review rounds: {cfg.review_max_rounds} (min score {cfg.review_min_score})")

    if not cfg.api_key:
        print("\nERROR: DEEPSEEK_API_KEY not set.", file=sys.stderr)
        print("  Copy .env.example to .env and add your key, or:", file=sys.stderr)
        print("  export DEEPSEEK_API_KEY=sk-...", file=sys.stderr)
        return 1

    try:
        pipeline = Pipeline(cfg, verbose=True)
        report = pipeline.run(
            source=source,
            max_rounds=args.max_rounds,
            skip_review=args.skip_review,
        )
    except LLMError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130

    if report.get("error"):
        print(f"\nPIPELINE FAILED: {report['error']}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
