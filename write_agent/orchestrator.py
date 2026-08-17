"""Research orchestrator CLI for the file-based experiment/writing bridge.

This module reads the model-optimize-loop registry without modifying it.  It
exports a bounded evidence snapshot, converts writing requests into the loop's
ExperimentPlan contract, and records completed loop runs as writing responses.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .experiment_bridge import ExperimentExchange, SCHEMA_VERSION, normalize_request


LOOP_EXPERIMENT_TYPES = {
    "BASELINE_REPRODUCTION",
    "HYPERPARAMETER_SEARCH",
    "ARCHITECTURE_CHANGE",
    "LOSS_CHANGE",
    "TRAINING_STRATEGY_CHANGE",
    "BUG_FIX",
    "RESOURCE_OPTIMIZATION",
    "ABLATION",
    "REPRODUCIBILITY_CHECK",
    "CONFIRMATION_RUN",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _registry_path(loop_root: Path) -> Path:
    return loop_root / "registry" / "experiments.db"


def _connect(loop_root: Path) -> sqlite3.Connection:
    db_path = _registry_path(loop_root)
    if not db_path.exists():
        raise ValueError(
            f"Experiment registry not found: {db_path}. "
            "Run/initialize model-optimize-loop first."
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _metrics(conn: sqlite3.Connection, experiment_id: str) -> dict[str, float]:
    if not _table_exists(conn, "metrics"):
        return {}
    rows = conn.execute(
        "SELECT metric_name, value FROM metrics WHERE experiment_id=? ORDER BY metric_name",
        (experiment_id,),
    ).fetchall()
    return {str(row["metric_name"]): float(row["value"]) for row in rows}


def _artifacts(conn: sqlite3.Connection, experiment_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "artifacts"):
        return []
    rows = conn.execute(
        "SELECT artifact_id, type, uri, sha256, size, retention "
        "FROM artifacts WHERE experiment_id=? ORDER BY artifact_id",
        (experiment_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _decisions(conn: sqlite3.Connection, experiment_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        "SELECT decision, reason, evidence, hypothesis_outcome, decided_by, created_at "
        "FROM decisions WHERE experiment_id=? ORDER BY id",
        (experiment_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.get("evidence") or "[]")
        except json.JSONDecodeError:
            item["evidence"] = [item.get("evidence")]
        result.append(item)
    return result


def _experiment(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "experiments"):
        return None
    row = conn.execute(
        "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["config"] = json.loads(item.get("config") or "{}")
    except json.JSONDecodeError:
        pass
    item["metrics"] = _metrics(conn, experiment_id)
    item["decisions"] = _decisions(conn, experiment_id)
    item["artifacts"] = _artifacts(conn, experiment_id)
    return item


def export_evidence_bundle(loop_root: Path, output: Path) -> dict[str, Any]:
    loop_root = loop_root.resolve()
    goal = _load_optional_json(loop_root / "state" / "research_goal.json")
    state = _load_optional_json(loop_root / "state" / "research_state.json")
    conn = _connect(loop_root)
    try:
        if not _table_exists(conn, "experiments"):
            rows = []
        else:
            rows = conn.execute(
                "SELECT experiment_id FROM experiments ORDER BY created_at, experiment_id"
            ).fetchall()
        experiments = [
            item
            for row in rows
            if (item := _experiment(conn, str(row["experiment_id"]))) is not None
        ]
    finally:
        conn.close()

    goal_id = str(goal.get("goal_id") or state.get("goal_id") or "GOAL-UNKNOWN")
    primary = goal.get("primary_metric") if isinstance(goal.get("primary_metric"), dict) else {}
    score = {
        "name": primary.get("name", "normalized_score"),
        "direction": primary.get("direction", "minimize"),
        "target": primary.get("target"),
        "min_improvement": primary.get("min_improvement"),
    }

    best_ref = state.get("best_run") if isinstance(state.get("best_run"), dict) else {}
    best_id = best_ref.get("experiment_id")
    by_id = {e["experiment_id"]: e for e in experiments}
    best_run = by_id.get(best_id, best_ref or None)
    if best_run is None:
        for experiment in reversed(experiments):
            if any(d.get("decision") == "KEEP_AS_BEST" for d in experiment["decisions"]):
                best_run = experiment
                break

    baseline_id = state.get("baseline_run") or goal.get("baseline_run")
    baseline = by_id.get(baseline_id) if baseline_id else None
    report_paths = [
        str(path.resolve())
        for path in sorted((loop_root / "reports").glob("*.md"))
    ] if (loop_root / "reports").exists() else []
    known_gaps = [
        {
            "experiment_id": experiment["experiment_id"],
            "decision": decision.get("decision"),
            "reason": decision.get("reason"),
        }
        for experiment in experiments
        for decision in experiment["decisions"][-1:]
        if decision.get("decision") in {"REVISE_HYPOTHESIS", "REQUEST_HUMAN_REVIEW", "RUN_ABLATION", "RUN_CONFIRMATION"}
    ]
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "goal_id": goal_id,
        "score": score,
        "baseline": baseline,
        "best_run": best_run,
        "experiments": experiments,
        "aggregate_results": {},
        "claims_supported": [],
        "known_gaps": known_gaps,
        "reports": report_paths,
        "provenance": {
            "loop_root": str(loop_root),
            "registry": str(_registry_path(loop_root).resolve()),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def _compact_lithobench_result(path: Path) -> dict[str, Any]:
    """Read one domain result while excluding bulky epoch/per-sample arrays."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Invalid LithoBench result JSON {path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw.get("experiment_id"):
        raise ValueError(f"LithoBench result lacks experiment_id: {path}")
    official = raw.get("official_evaluation") or {}
    objective = raw.get("objective") or {}
    proxy = raw.get("proxy_evaluation") or {}

    def compact_proxy(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict):
            return {}
        keys = (
            "epoch", "total", "metric_l2", "metric_pvband", "metric_iou",
            "gradient_norm", "seconds",
        )
        return {key: record.get(key) for key in keys if record.get(key) is not None}

    return {
        "experiment_id": raw.get("experiment_id"),
        "parent_experiment_id": raw.get("parent_experiment_id"),
        "status": raw.get("status"),
        "created_at_utc": raw.get("created_at_utc"),
        "duration_seconds": raw.get("duration_seconds"),
        "hypothesis": raw.get("hypothesis"),
        "budget": raw.get("budget"),
        "protocol_name": raw.get("protocol_name"),
        "changes": raw.get("normalized_changes") or {},
        "seed": (raw.get("normalized_changes") or {}).get("train.seed"),
        "proxy_summary": {
            "epochs_recorded": proxy.get("epochs_recorded"),
            "last_validation": compact_proxy(proxy.get("last_validation")),
            "best_validation": compact_proxy(proxy.get("best_validation")),
        },
        "official_metrics": official.get("mean") or {},
        "objective": {
            "normalized_score": objective.get("normalized_score"),
            "terms": objective.get("terms") or {},
            "constraints": objective.get("constraints") or {},
            "passed": objective.get("passed"),
        },
        "artifacts": raw.get("artifacts") or {},
        "result_path": str(path.resolve()),
    }


def export_lithobench_bundle(
    lithobench_root: Path,
    output: Path,
    *,
    best_run_id: str | None = None,
    baseline_run_id: str | None = None,
) -> dict[str, Any]:
    """Export existing LithoBench result.json files directly for writing.

    This supports historical campaigns that predate (or bypassed) the generic
    model-optimize-loop SQLite registry.
    """
    lithobench_root = lithobench_root.resolve()
    runs_root = lithobench_root / "runs" / "agent_experiments"
    if not runs_root.exists():
        raise ValueError(f"LithoBench experiment runs directory not found: {runs_root}")
    paths = sorted(runs_root.glob("*/result.json"))
    if not paths:
        raise ValueError(f"No result.json files found under {runs_root}")
    experiments = [_compact_lithobench_result(path) for path in paths]
    by_id = {str(item["experiment_id"]): item for item in experiments}

    scored = [
        item for item in experiments
        if isinstance(item.get("objective", {}).get("normalized_score"), (int, float))
        and item.get("status") == "completed"
    ]
    if best_run_id:
        if best_run_id not in by_id:
            raise ValueError(f"Requested best run not found: {best_run_id}")
        best_run = by_id[best_run_id]
    else:
        best_run = min(
            scored,
            key=lambda item: float(item["objective"]["normalized_score"]),
        ) if scored else None

    if baseline_run_id:
        if baseline_run_id not in by_id:
            raise ValueError(f"Requested baseline run not found: {baseline_run_id}")
        baseline = by_id[baseline_run_id]
    else:
        baseline_candidates = [
            item for item in experiments
            if "BASELINE" in str(item.get("experiment_id", "")).upper()
        ]
        baseline = baseline_candidates[-1] if baseline_candidates else None

    campaign_summaries: list[dict[str, Any]] = []
    for summary_path in sorted((lithobench_root / "runs").glob("campaign_*_summary.json")):
        try:
            summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        campaign_summaries.append({
            "path": str(summary_path.resolve()),
            "data": summary_data,
        })

    scores = [float(item["objective"]["normalized_score"]) for item in scored]
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "goal_id": "GOAL-ILT-001",
        "score": {
            "name": "normalized_score",
            "direction": "minimize",
            "observed_min": min(scores) if scores else None,
            "observed_max": max(scores) if scores else None,
        },
        "baseline": baseline,
        "best_run": best_run,
        "experiments": experiments,
        "aggregate_results": {
            "result_count": len(experiments),
            "completed_count": sum(item.get("status") == "completed" for item in experiments),
            "scored_count": len(scored),
            "campaign_summary_count": len(campaign_summaries),
        },
        "campaign_summaries": campaign_summaries,
        "claims_supported": [],
        "known_gaps": [],
        "provenance": {
            "source": "lithobench_result_files",
            "lithobench_root": str(lithobench_root),
            "runs_root": str(runs_root),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def request_to_experiment_plan(
    request: dict[str, Any], goal_id: str, workspace: str | None = None
) -> dict[str, Any]:
    request = normalize_request(request)
    requested_type = str(request.get("type", "CONFIRMATION_RUN")).upper()
    experiment_type = requested_type if requested_type in LOOP_EXPERIMENT_TYPES else "CONFIRMATION_RUN"
    request_id = str(request["request_id"])
    suffix = request_id.replace("WR-EXP-", "")[:16]
    required_metrics = request.get("required_metrics") or []
    expected_metrics = {str(metric): "report exact value" for metric in required_metrics}
    return {
        "experiment_id": f"WRITE-{suffix}",
        "parent_run_id": request.get("baseline_run_id"),
        "goal_id": goal_id,
        "type": experiment_type,
        "risk_level": request.get("risk_level", "L1"),
        "hypothesis": request.get("hypothesis") or request.get("question"),
        "changes": {
            "writing_request_id": request_id,
            "question": request.get("question"),
            "variables": request.get("variables", {}),
            "controlled_variables": request.get("controlled_variables", []),
            "seeds": request.get("seeds", []),
            "requested_artifacts": request.get("requested_artifacts", []),
            "protocol_summary": request.get("protocol_summary", ""),
        },
        "changed_files": [],
        "expected_metrics": expected_metrics,
        "success_condition": request.get("success_condition", ""),
        "failure_condition": request.get("failure_condition", "Evidence remains insufficient"),
        "rollback_strategy": "Use model-optimize-loop approval and rollback policy",
        "estimated_gpu_hours": float(request.get("estimated_gpu_hours", 0.0)),
        "approval_required": bool(request.get("approval_required", True)),
        "workspace": workspace,
        "created_at": _utcnow(),
    }


def prepare_requests(
    paper_dir: Path,
    output_dir: Path,
    goal_id: str,
    workspace: str | None = None,
) -> list[Path]:
    exchange = ExperimentExchange(paper_dir)
    latest = exchange.latest_responses()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for request in exchange.requests():
        request_id = str(request.get("request_id", ""))
        if not request_id or request_id in latest:
            continue
        plan = request_to_experiment_plan(request, goal_id=goal_id, workspace=workspace)
        target = output_dir / f"{plan['experiment_id']}.json"
        target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(target)
    return written


def record_response(
    paper_dir: Path,
    loop_root: Path,
    request_id: str,
    experiment_id: str,
) -> dict[str, Any]:
    conn = _connect(loop_root.resolve())
    try:
        experiment = _experiment(conn, experiment_id)
    finally:
        conn.close()
    if experiment is None:
        raise ValueError(f"Experiment not found in registry: {experiment_id}")
    decisions = experiment.get("decisions", [])
    last_decision = decisions[-1] if decisions else {}
    response = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "status": experiment.get("status", "COMPLETED"),
        "experiment_ids": [experiment_id],
        "metrics": experiment.get("metrics", {}),
        "aggregate_results": {},
        "decision": last_decision.get("decision"),
        "reason": last_decision.get("reason"),
        "evidence": last_decision.get("evidence", []),
        "artifacts": experiment.get("artifacts", []),
        "limitations": [],
        "provenance": {
            "registry": str(_registry_path(loop_root.resolve()).resolve()),
            "experiment_id": experiment_id,
            "commit": experiment.get("commit"),
            "seed": experiment.get("seed"),
        },
        "created_at": _utcnow(),
    }
    return ExperimentExchange(paper_dir).append_response(response)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Writing/experiment research orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export-bundle", help="Export model-optimize-loop registry evidence")
    export.add_argument("--loop-root", required=True)
    export.add_argument("--output", required=True)

    litho = sub.add_parser(
        "export-lithobench-bundle",
        help="Export existing LithoBench result.json evidence directly",
    )
    litho.add_argument("--lithobench-root", required=True)
    litho.add_argument("--output", required=True)
    litho.add_argument("--best-run", default=None)
    litho.add_argument("--baseline-run", default=None)

    prepare = sub.add_parser("prepare-requests", help="Convert pending writing requests to ExperimentPlan JSON")
    prepare.add_argument("--paper-dir", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--goal-id", required=True)
    prepare.add_argument("--workspace", default=None)

    response = sub.add_parser("record-response", help="Record a completed registry run for the writer")
    response.add_argument("--paper-dir", required=True)
    response.add_argument("--loop-root", required=True)
    response.add_argument("--request-id", required=True)
    response.add_argument("--experiment-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export-bundle":
            bundle = export_evidence_bundle(Path(args.loop_root), Path(args.output))
            print(f"Exported {len(bundle['experiments'])} experiments to {args.output}")
        elif args.command == "export-lithobench-bundle":
            bundle = export_lithobench_bundle(
                Path(args.lithobench_root),
                Path(args.output),
                best_run_id=args.best_run,
                baseline_run_id=args.baseline_run,
            )
            print(
                f"Exported {len(bundle['experiments'])} LithoBench results "
                f"to {args.output}"
            )
        elif args.command == "prepare-requests":
            paths = prepare_requests(
                Path(args.paper_dir), Path(args.output_dir), args.goal_id, args.workspace
            )
            for path in paths:
                print(path)
            print(f"Prepared {len(paths)} experiment plan(s)")
        elif args.command == "record-response":
            response = record_response(
                Path(args.paper_dir), Path(args.loop_root), args.request_id, args.experiment_id
            )
            print(json.dumps(response, ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
