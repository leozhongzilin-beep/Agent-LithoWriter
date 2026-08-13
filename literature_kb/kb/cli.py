"""Command-line interface for the Literature Knowledge Base.

Write side:
    kb init                     create directories + schema + FTS index
    kb add  <package>           import a package (JSON/YAML)
            [--source FILE] [--paper-id ID] [--force]
    kb list [--domain X] [--year N]

Read side (retrieval):
    kb search "q" --intent DISCOVERY [--filter k=v ...] [--max-tokens N]
    kb get-card <paper_id>
    kb metrics <paper_id> [--metric EPE]
    kb verify "claim" [--paper <id>]
    kb formula "q" [--role loss]
    kb cite <paper_id> [--style ieee]

    kb status                   KB health: counts, dangling sources
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import chunker, csl, schema, store
from . import package as pkg
from .config import resolve_kb_root
from .importtool import ImportBlocked, import_package
from .retrieve import RetrievalService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb",
        description="Literature Knowledge Base storage + retrieval tools.",
    )
    parser.add_argument("--root", help="KB_ROOT directory (default: $KB_ROOT or literature_kb/data)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- write side ----
    p_init = sub.add_parser("init", help="create directories and schema")
    p_init.add_argument("--root", dest="init_root")

    p_add = sub.add_parser("add", help="import a canonical paper package")
    p_add.add_argument("package")
    p_add.add_argument("--root", dest="add_root")
    p_add.add_argument("--source", help="original source document to archive")
    p_add.add_argument("--paper-id", help="explicit paper_id override")
    p_add.add_argument("--force", action="store_true")
    p_add.add_argument("--embed", action="store_true",
                       help="embed the paper right after import (skipped if "
                            "no embedding model is available)")

    p_list = sub.add_parser("list", help="list papers")
    p_list.add_argument("--root", dest="list_root")
    p_list.add_argument("--domain")
    p_list.add_argument("--year", type=int)

    # ---- read side ----
    p_search = sub.add_parser("search", help="retrieve by intent (DISCOVERY/...)")
    p_search.add_argument("query")
    p_search.add_argument("--root", dest="search_root")
    p_search.add_argument("--intent", default="DISCOVERY",
                          choices=["DISCOVERY", "CITATION", "TECHNICAL", "RESULT",
                                   "FORMULA", "VERIFICATION", "COMPARISON"])
    p_search.add_argument("--filter", action="append", default=[],
                          help="metadata filter, e.g. --filter domain=ILT --filter year_from=2020")
    p_search.add_argument("--max-tokens", type=int)

    p_card = sub.add_parser("get-card", help="L1 paper card")
    p_card.add_argument("paper_id")
    p_card.add_argument("--root", dest="card_root")

    p_metrics = sub.add_parser("metrics", help="L2 structured results")
    p_metrics.add_argument("paper_id")
    p_metrics.add_argument("--root", dest="metrics_root")
    p_metrics.add_argument("--metric", action="append", default=[])

    p_verify = sub.add_parser("verify", help="check whether a claim is supported")
    p_verify.add_argument("claim")
    p_verify.add_argument("--root", dest="verify_root")
    p_verify.add_argument("--paper", action="append", default=[])

    p_formula = sub.add_parser("formula", help="search the formula KB")
    p_formula.add_argument("query")
    p_formula.add_argument("--root", dest="formula_root")
    p_formula.add_argument("--role")

    p_cite = sub.add_parser("cite", help="resolve a rendered citation")
    p_cite.add_argument("paper_id")
    p_cite.add_argument("--root", dest="cite_root")
    p_cite.add_argument("--style")

    p_chunk = sub.add_parser("chunk", help="chunk an archived source into L4 paragraphs")
    p_chunk.add_argument("paper_id")
    p_chunk.add_argument("--root", dest="chunk_root")
    p_chunk.add_argument("--source", help="explicit source file (default: archived copy)")

    p_chunks = sub.add_parser("chunks", help="print a paper's L4 chunks")
    p_chunks.add_argument("paper_id")
    p_chunks.add_argument("--root", dest="chunks_root")

    p_embed = sub.add_parser("embed", help="embed papers for vector retrieval")
    p_embed.add_argument("--root", dest="embed_root")
    p_embed.add_argument("--paper", help="embed only this paper (default: all)")

    p_seed = sub.add_parser("seed-ontology", help="load curated concepts + metrics")
    p_seed.add_argument("--root", dest="seed_root")

    p_biblio = sub.add_parser("bibliography", help="render a full CSL reference list")
    p_biblio.add_argument("style_id")
    p_biblio.add_argument("--root", dest="biblio_root")
    p_biblio.add_argument("--paper", action="append", default=[],
                          help="restrict to paper(s); default: all papers")

    p_status = sub.add_parser("status", help="KB health")
    p_status.add_argument("--root", dest="status_root")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return _cmd_init(resolve_kb_root(args.init_root))
        if args.command == "add":
            return _cmd_add(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "search":
            return _cmd_search(args)
        if args.command == "get-card":
            return _cmd_card(args)
        if args.command == "metrics":
            return _cmd_metrics(args)
        if args.command == "verify":
            return _cmd_verify(args)
        if args.command == "formula":
            return _cmd_formula(args)
        if args.command == "cite":
            return _cmd_cite(args)
        if args.command == "chunk":
            return _cmd_chunk(args)
        if args.command == "chunks":
            return _cmd_chunks(args)
        if args.command == "embed":
            return _cmd_embed(args)
        if args.command == "seed-ontology":
            return _cmd_seed_ontology(args)
        if args.command == "bibliography":
            return _cmd_bibliography(args)
        if args.command == "status":
            return _cmd_status(args)
    except (pkg.PackageError, ImportBlocked) as exc:
        _die(str(exc))
    return 0


def _require_store(root: Path) -> store.KBStore:
    kbs = store.KBStore(root)
    if not kbs.is_initialized:
        _die(f"KB not initialized at {root}; run `kb init` first")
    return kbs


def _cmd_init(root: Path) -> int:
    kbs = store.KBStore(root)
    kbs.init()
    csl.seed_styles(kbs)  # register bundled CSL styles
    print(f"initialized KB at {kbs.root}")
    print(f"  db:       {kbs.db_path}")
    print(f"  schema:   v{schema.SCHEMA_VERSION}")
    print(f"  styles:   {', '.join(csl.bundled_style_ids()) or '-'}")
    return 0


def _cmd_add(args) -> int:
    root = resolve_kb_root(args.add_root)
    kbs = _require_store(root)
    data = pkg.load_package(args.package)
    result = import_package(
        kbs,
        data,
        source_path=args.source,
        paper_id_override=args.paper_id,
        force=args.force,
        imported_from=str(Path(args.package).resolve()),
    )
    print(f"[{result.decision}] {result.paper_id}")
    if result.matches:
        for key, val in result.matches:
            print(f"  matched via {key}: {val}")
    total = sum(result.row_counts.values())
    print(f"  rows written: {total}  ({len(result.row_counts)} tables)")
    if result.warnings:
        print("  warnings:")
        for w in result.warnings:
            print(f"    - {w}")
    if result.archive_package:
        print(f"  archive:  {result.archive_package}")
    if args.embed:
        _embed_after_add(kbs, result.paper_id)
    return 0


def _embed_after_add(kbs, paper_id: str) -> None:
    """Import-time embed hook: degrade gracefully when no model is available."""
    from . import embedder, vectors

    try:
        emb = embedder.get_embedder()
        counts = vectors.embed_paper(kbs, emb, paper_id)
    except embedder.EmbedderUnavailable as exc:
        print(f"  embedding skipped: {exc}")
        return
    print(f"  embedded: {sum(counts.values())} objects")


def _cmd_list(args) -> int:
    kbs = _require_store(resolve_kb_root(args.list_root))
    papers = kbs.list_papers(domain=args.domain, year=args.year)
    if not papers:
        print("(no papers)")
        return 0
    for p in papers:
        doi = f"  doi={p['doi']}" if p["doi"] else ""
        print(f"{p['paper_id']}  {p['year']}  {p['citation_key']}  {p['title']}{doi}")
    print(f"\n{len(papers)} paper(s)")
    return 0


# ---------------------------------------------------------------------------
# retrieval commands
# ---------------------------------------------------------------------------

def _cmd_search(args) -> int:
    kbs = _require_store(resolve_kb_root(args.search_root))
    svc = RetrievalService(kbs)
    rs = svc.retrieve(
        args.query, args.intent,
        filters=_parse_filters(args.filter),
        max_tokens=args.max_tokens,
    )
    print(f"mode: {rs.mode}   next: {rs.next_action or '-'}   "
          f"truncated: {rs.truncated}")
    if not rs.results:
        print("(no results)")
        return 0
    for r in rs.results:
        print(f"[{r.relevance:.3f}] {r.paper_id}  {r.citation_key}  {r.citation}")
        print(f"      {r.title}")
        if r.key_fact:
            print(f"      key: {r.key_fact[:140]}")
        if r.evidence_ids:
            print(f"      evidence: {', '.join(r.evidence_ids)}")
    return 0


def _cmd_card(args) -> int:
    kbs = _require_store(resolve_kb_root(args.card_root))
    card = RetrievalService(kbs).get_card(args.paper_id)
    if card is None:
        _die(f"no L1 card for {args.paper_id}")
    _print_card(card)
    return 0


def _cmd_metrics(args) -> int:
    kbs = _require_store(resolve_kb_root(args.metrics_root))
    structured = RetrievalService(kbs).structured(
        args.paper_id, metrics=args.metric or None)
    for m in structured["metrics"]:
        cond = m.get("condition") or {}
        cond_s = ", ".join(f"{k}={v}" for k, v in cond.items())
        print(f"{m['name']}={m.get('value_text') or m.get('value') or '?'} "
              f"{m.get('unit') or ''}  status={m['status']}  ({cond_s})")
        if m.get("source_evidence_id"):
            page = m.get("source_page")
            page_s = f" page={page}" if page else ""
            print(f"    evidence: {m['source_evidence_id']}{page_s}")
    for c in structured["comparisons"]:
        print(f"comparison: {c['metric']} validity={c['comparison_validity']}")
    return 0


def _cmd_verify(args) -> int:
    kbs = _require_store(resolve_kb_root(args.verify_root))
    v = RetrievalService(kbs).verify(args.claim, candidate_papers=args.paper or None)
    print(f"verdict: {v.verdict}   strength: {v.strength or '-'}")
    for e in v.evidence:
        print(f"  [{e.relevance:.2f}] {e.paper_id} {e.evidence_id}"
              f"{f' p.{e.page}' if e.page else ''}: {e.source_text[:160]}")
    for note in v.notes:
        print(f"  note: {note}")
    return 0


def _cmd_formula(args) -> int:
    kbs = _require_store(resolve_kb_root(args.formula_root))
    hits = RetrievalService(kbs).formulas(args.query, role=args.role)
    if not hits:
        print("(no formulas)")
        return 0
    for f in hits:
        vars_s = ", ".join(f"{v['symbol']}={v['meaning']}" for v in f.variables)
        print(f"[{f.formula_role}] {f.paper_id} {f.formula_id}: {f.formula_latex}")
        if f.semantic_description:
            print(f"    {f.semantic_description}")
        if vars_s:
            print(f"    vars: {vars_s}")
        if f.source_evidence_id:
            print(f"    evidence: {f.source_evidence_id}")
    return 0


def _cmd_cite(args) -> int:
    kbs = _require_store(resolve_kb_root(args.cite_root))
    out = RetrievalService(kbs).cite(args.paper_id, style_id=args.style)
    if out is None:
        _die(f"no paper {args.paper_id}")
    print(f"citation_key: {out['citation_key']}")
    print(f"in-text:      {out['in_text_citation']}")
    if out["bibliography_entry"]:
        print(f"bibliography: {out['bibliography_entry']}")
    print(f"generated:    {out['generated']}")
    return 0


def _cmd_chunk(args) -> int:
    kbs = _require_store(resolve_kb_root(args.chunk_root))
    src = Path(args.source) if args.source else kbs.source_archived(args.paper_id)
    if src is None:
        _die(f"no archived source for {args.paper_id}; pass --source")
    try:
        doc = chunker.chunk_source(args.paper_id, src)
    except chunker.UnsupportedFormat as exc:
        _die(str(exc))
    chunker.store_chunks(kbs, args.paper_id, doc)
    print(f"chunked {len(doc.chunks)} chunks across {len(doc.sections)} sections")
    print(f"  title: {doc.title or '-'}")
    return 0


def _cmd_chunks(args) -> int:
    kbs = _require_store(resolve_kb_root(args.chunks_root))
    chunks = chunker.get_chunks(kbs, args.paper_id)
    if not chunks:
        print("(no chunks)")
        return 0
    for c in chunks:
        print(f"[{c['section']} #{c['paragraph_index']}] {c['text'][:160]}")
    return 0


def _cmd_embed(args) -> int:
    from . import embedder, vectors

    kbs = _require_store(resolve_kb_root(args.embed_root))
    try:
        emb = embedder.get_embedder()
        ids = [args.paper] if args.paper else kbs.all_paper_ids()
        if not ids:
            print("(no papers to embed)")
            return 0
        total: dict[str, int] = {}
        for pid in ids:
            for kind, n in vectors.embed_paper(kbs, emb, pid).items():
                total[kind] = total.get(kind, 0) + n
    except embedder.EmbedderUnavailable as exc:
        _die(str(exc))
    print(f"embedded {sum(total.values())} objects across {len(ids)} paper(s) "
          f"({', '.join(f'{k}={v}' for k, v in total.items())})")
    return 0


def _cmd_seed_ontology(args) -> int:
    from . import ontology

    kbs = _require_store(resolve_kb_root(args.seed_root))
    data = ontology.load_seed()
    errors, warnings = ontology.validate_seed(data)
    if errors:
        _die("ontology seed failed validation:\n  - " + "\n  - ".join(errors))
    counts = ontology.seed(kbs, data)
    print(f"seeded ontology: concepts={counts['concepts']} "
          f"metrics={counts['metrics_ontology']}")
    if warnings:
        for w in warnings:
            print(f"  warn: {w}")
    return 0


def _cmd_bibliography(args) -> int:
    kbs = _require_store(resolve_kb_root(args.biblio_root))
    pids = args.paper if args.paper else kbs.all_paper_ids()
    if not pids:
        print("(no papers)")
        return 0
    try:
        entries = csl.render_bibliography(kbs, pids, args.style_id)
    except csl.StyleUnavailable as exc:
        _die(str(exc))
    if not entries:
        print("(no renderable bibliography)")
        return 0
    for i, entry in enumerate(entries, start=1):
        print(f"[{i}] {entry}")
    return 0


def _cmd_status(args) -> int:
    kbs = _require_store(resolve_kb_root(args.status_root))
    print(f"KB_ROOT:  {kbs.root}")
    print(f"db:       {kbs.db_path}")
    print("\ncollection counts:")
    for table, count in kbs.table_counts().items():
        print(f"  {table:<20} {count}")
    dangling = kbs.dangling_sources()
    print(f"\ndangling sources: {len(dangling)}")
    for pid in dangling:
        print(f"  - {pid}")
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_filters(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            _die(f"filter must be key=value: {pair!r}")
        key, _, value = pair.partition("=")
        if key in ("year_from", "year_to"):
            out[key] = int(value)
        else:
            out[key] = value
    return out


def _print_card(card: dict[str, Any]) -> None:
    print(f"{card.get('paper_id')}  {card.get('title')}")
    for field in ("research_problem", "research_gap", "main_idea",
                  "method_summary", "innovation", "key_findings_summary"):
        val = card.get(field)
        if val:
            print(f"  {field}: {val}")
    ru = card.get("recommended_use") or {}
    if ru:
        print("  recommended_use: " + ", ".join(f"{k}={v}" for k, v in ru.items()))


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
