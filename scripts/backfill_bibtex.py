"""One-off backfill: repair a paper whose bibtex/doi was stripped at import.

Root cause of the data gap: a wrong Crossref DOI (title search) left the
``citation_cache`` empty, so the paper became uncitable. This script fetches
the canonical BibTeX for the correct DOI (never-guess gate) and patches the
paper's ``doi`` / ``bibtex_key`` / ``citation_cache`` in place.

Usage (from writing-agent/):
    # dry-run (default): fetch + print, change nothing
    python scripts/backfill_bibtex.py --paper-id ILT_2022_011 --doi 10.1109/TCAD.2021.3061494
    # apply after verifying the printed title matches
    python scripts/backfill_bibtex.py --paper-id ILT_2022_011 --doi 10.1109/TCAD.2021.3061494 --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # writing-agent/
for _pkg in (_ROOT / "literature_kb", _ROOT):
    if str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

from kb import KBStore

from paper2kb.metadata import fetch_bibtex

DEFAULT_KB_ROOT = _ROOT / "literature_kb" / "data"


def _norm(s: str) -> str:
    return re.sub(r"[{}]", "", s).lower()


def _bibtex_title(bibtex: str) -> str:
    m = re.search(r"title\s*=\s*\{([^}]*)\}", bibtex, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill a paper's bibtex/doi.")
    ap.add_argument("--paper-id", required=True)
    ap.add_argument("--doi", required=True)
    ap.add_argument("--root", default=str(DEFAULT_KB_ROOT))
    ap.add_argument("--apply", action="store_true",
                    help="write to the KB (default: dry-run only)")
    args = ap.parse_args(argv)

    store = KBStore(Path(args.root))
    paper = store.get_paper(args.paper_id)
    if paper is None:
        print(f"error: no paper {args.paper_id}", file=sys.stderr)
        return 1
    stored_title = paper.get("title") or ""

    bibtex = fetch_bibtex(args.doi)
    if not bibtex or not bibtex.lstrip().startswith("@"):
        print(f"error: no BibTeX resolved for DOI {args.doi} (never-guess gate)",
              file=sys.stderr)
        return 1

    fetched_title = _bibtex_title(bibtex)
    print(f"paper_id:   {args.paper_id}")
    print(f"stored:     {stored_title}")
    print(f"fetched:    {fetched_title}")
    print("bibtex:")
    print(bibtex)
    print()

    if stored_title and fetched_title:
        a, b = _norm(stored_title), _norm(fetched_title)
        if a not in b and b not in a:
            print("WARNING: fetched bibtex title does not match stored title.",
                  file=sys.stderr)
            print("         Re-check the DOI before --apply.", file=sys.stderr)
            return 1

    if not args.apply:
        print("dry-run: no change. Re-run with --apply to write.")
        return 0

    store.update_citation_metadata(args.paper_id, args.doi, bibtex)
    from kb.retrieve import RetrievalService
    hits = RetrievalService(store).resolve_hint(stored_title)
    ok = any(h.bibtex for h in hits)
    print(f"backfilled {args.paper_id}: doi={args.doi}")
    print(f"self-check resolve_hint: {len(hits)} hit(s), bibtex_present={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
