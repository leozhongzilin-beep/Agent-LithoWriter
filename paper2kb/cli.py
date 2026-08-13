"""Command line: paper2kb <source> [--out pkg.json] [--doi X] [--title T]

Processes a paper source into a canonical KB package. Requires DEEPSEEK_API_KEY
for the per-layer LLM extraction (same convention as write_agent).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper2kb",
        description="Paper-to-Literature-KB: convert a paper source into the "
                    "canonical package that `kb add` ingests.",
    )
    p.add_argument("source", help="PDF / markdown / XML / LaTeX / text source")
    p.add_argument("--out", help="write the package JSON here (default: stdout summary)")
    p.add_argument("--doi", help="DOI for Crossref metadata resolution")
    p.add_argument("--title", help="title override (also used for Crossref search)")
    p.add_argument("--model", default="deepseek-chat",
                   help="DeepSeek model (default: deepseek-chat)")
    p.add_argument("--max-tokens", type=int, default=8192)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        llm = pipeline.make_llm(model=args.model, max_tokens=args.max_tokens)
    except OSError as exc:
        _die(str(exc))
    try:
        pkg = pipeline.process_paper(
            args.source, llm=llm, doi=args.doi, title=args.title)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: surface to user
        _die(f"processing failed: {exc}")
    return _report(pkg, out=args.out)


def _report(pkg: dict[str, Any], out: str | None) -> int:
    report = pkg["validation_report"]
    if out:
        Path(out).write_text(json.dumps(pkg, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"wrote {out}")
    else:
        L0 = pkg["paper"]["L0"]
        L2 = pkg["paper"]["L2"]
        L3 = pkg["paper"]["L3"]
        print(f"title:     {L0.get('title')}")
        print(f"source:    {pkg['source']['path']}  ({pkg['source']['type']})")
        print(f"metrics:   {len(L2['metrics'])}   evidence: {len(L3['evidence'])}"
              f"   formulas: {len(pkg['formulas'])}")
    print(f"validation: pass={report['pass']}  errors={len(report['errors'])}")
    for err in report["errors"]:
        print(f"  error: {err}")
    for trig in report["human_review"]:
        print(f"  human-review: {trig}")
    return 0 if report["pass"] else 1


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
