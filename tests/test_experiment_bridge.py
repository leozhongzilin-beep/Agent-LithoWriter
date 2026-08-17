from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from write_agent.config import load_config
from write_agent.experiment_bridge import (
    EvidenceBundle,
    ExperimentExchange,
    evidence_update_context,
    normalize_request,
    requests_from_missing_claims,
)
from write_agent.orchestrator import (
    export_evidence_bundle,
    export_lithobench_bundle,
    record_response,
    request_to_experiment_plan,
)
from write_agent.pipeline import Pipeline
from write_agent.phases.review import _parse_review_json, run_review_loop


def _bundle(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "goal_id": "GOAL-ILT-001",
        "score": {"name": "normalized_score", "direction": "minimize"},
        "baseline": {"run_id": "base", "metrics": {"normalized_score": 1.0}},
        "best_run": {"run_id": "best", "metrics": {"normalized_score": 0.84}},
        "experiments": [],
        "artifacts": [],
    }), encoding="utf-8")
    return path


def test_evidence_bundle_and_exchange(tmp_path: Path):
    bundle = EvidenceBundle.load(_bundle(tmp_path / "bundle.json"))
    assert "normalized_score" in bundle.prompt_context()

    exchange = ExperimentExchange(tmp_path / "paper")
    request = requests_from_missing_claims([{
        "claim": "Dual history improves EPE",
        "evidence": "paired ablation",
        "status": "needs_experiment",
        "section": "4",
    }])[0]
    exchange.append_request(request)
    exchange.append_request(request)
    assert len(exchange.requests()) == 1
    assert exchange.unresolved([request["request_id"]]) == [request["request_id"]]

    exchange.append_response({
        "request_id": request["request_id"],
        "status": "COMPLETED",
        "experiment_ids": ["EXP-1"],
        "metrics": {"EPE": 5.5},
    })
    assert exchange.unresolved([request["request_id"]]) == []
    assert exchange.completed_for([request["request_id"]])[0]["metrics"]["EPE"] == 5.5


def test_evidence_update_context_prioritizes_new_response(tmp_path: Path):
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps({
        "schema_version": "1.0",
        "goal_id": "GOAL-ILT-001",
        "padding": "x" * 20000,
    }), encoding="utf-8")
    bundle = EvidenceBundle.load(bundle_path)
    response = {
        "request_id": "WR-EXP-NEW",
        "status": "COMPLETED",
        "aggregate_results": {
            "metrics": {"normalized_score": {"mean": 0.9540403953}}
        },
        "comparison": {"claim_supported": False},
    }

    request = {
        "request_id": "WR-EXP-NEW",
        "question": "Is hc32 better than hc24?",
        "origin": {"section": "Section 5.2"},
    }
    response["comparison"].update({
        "target_normalized_score_mean": 0.9540403953,
        "reference_normalized_score_mean": 0.9445,
    })
    bundle.data["score"] = {"direction": "minimize"}

    context = evidence_update_context(bundle, [response], [request])

    assert context.index("NEW REQUEST-RESPONSE HANDOFFS") < context.index(
        "STRUCTURED EXPERIMENT EVIDENCE"
    )
    assert "0.9540403953" in context
    assert "reference is better" in context
    assert "Is hc32 better than hc24?" in context


def test_request_maps_to_loop_experiment_plan():
    request = requests_from_missing_claims([{
        "claim": "The method is robust across seeds",
        "evidence": "five-seed confirmation",
        "status": "needs_experiment",
        "section": "4",
    }])[0]
    plan = request_to_experiment_plan(request, "GOAL-ILT-001", workspace="D:/repo")
    assert plan["type"] == "CONFIRMATION_RUN"
    assert plan["goal_id"] == "GOAL-ILT-001"
    assert plan["approval_required"] is True
    assert plan["changes"]["writing_request_id"] == request["request_id"]


def test_legacy_review_request_is_normalized_for_automatic_execution():
    request = normalize_request({
        "question": "Compare the hc24 baseline with hc32.",
        "experiment": "Run the hc24 baseline with the same 5 seeds (2026-2030) and report Score.",
        "budget": "5 training runs at 8 epochs, approximately 10 GPU-hours",
        "success_metric": "A mean Score difference greater than 0.02 supports the claim.",
    })
    assert request["seeds"] == [2026, 2027, 2028, 2029, 2030]
    assert request["required_metrics"] == ["normalized_score"]
    assert request["estimated_gpu_hours"] == 10.0
    assert request["success_condition"].startswith("A mean Score")


def test_orchestrator_exports_registry_and_records_response(tmp_path: Path):
    loop_root = tmp_path / "loop"
    registry = loop_root / "registry"
    registry.mkdir(parents=True)
    conn = sqlite3.connect(registry / "experiments.db")
    conn.executescript("""
        CREATE TABLE experiments (
            experiment_id TEXT PRIMARY KEY, status TEXT, config TEXT,
            created_at TEXT, `commit` TEXT, seed INTEGER
        );
        CREATE TABLE metrics (
            experiment_id TEXT, metric_name TEXT, value REAL
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY, experiment_id TEXT, decision TEXT,
            reason TEXT, evidence TEXT, hypothesis_outcome TEXT,
            decided_by TEXT, created_at TEXT
        );
        CREATE TABLE artifacts (
            artifact_id TEXT, experiment_id TEXT, type TEXT, uri TEXT,
            sha256 TEXT, size INTEGER, retention TEXT
        );
    """)
    conn.execute(
        "INSERT INTO experiments VALUES (?,?,?,?,?,?)",
        ("EXP-1", "COMPLETED", "{}", "2026-08-14T00:00:00Z", "abc", 2028),
    )
    conn.execute("INSERT INTO metrics VALUES (?,?,?)", ("EXP-1", "EPE", 5.5))
    conn.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
        (1, "EXP-1", "KEEP_AS_BEST", "better EPE", '["EPE=5.5"]',
         "CONFIRMED", "test", "2026-08-14T00:01:00Z"),
    )
    conn.execute(
        "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
        ("A-1", "EXP-1", "metric_table", "results.json", "deadbeef", 20, "keep"),
    )
    conn.commit()
    conn.close()

    bundle_path = tmp_path / "bundle.json"
    bundle = export_evidence_bundle(loop_root, bundle_path)
    assert bundle["best_run"]["experiment_id"] == "EXP-1"
    assert bundle["experiments"][0]["metrics"]["EPE"] == 5.5

    paper_dir = tmp_path / "paper"
    response = record_response(paper_dir, loop_root, "WR-EXP-TEST", "EXP-1")
    assert response["decision"] == "KEEP_AS_BEST"
    assert ExperimentExchange(paper_dir).responses()[0]["artifacts"][0]["artifact_id"] == "A-1"


def test_orchestrator_exports_existing_lithobench_results(tmp_path: Path):
    litho = tmp_path / "lithobench"
    run_dir = litho / "runs" / "agent_experiments" / "R18_seed2028"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "experiment_id": "R18_seed2028",
        "parent_experiment_id": "R14_seed2028",
        "hypothesis": "Longer training improves the paired score.",
        "normalized_changes": {"train.seed": 2028},
        "proxy_evaluation": {
            "epochs_recorded": 16,
            "best_validation": {"epoch": 14, "total": 0.07, "unused": 999},
        },
        "official_evaluation": {"mean": {"l2": 37807.9, "pvb": 37686.0, "epe": 5.5}},
        "objective": {"normalized_score": 0.8418, "passed": True},
        "artifacts": {"checkpoint": "best.pt"},
    }), encoding="utf-8")

    output = tmp_path / "real_bundle.json"
    bundle = export_lithobench_bundle(
        litho, output, best_run_id="R18_seed2028"
    )
    assert bundle["aggregate_results"]["result_count"] == 1
    assert bundle["best_run"]["official_metrics"]["epe"] == 5.5
    assert "unused" not in bundle["experiments"][0]["proxy_summary"]["best_validation"]
    assert EvidenceBundle.load(output).data["provenance"]["source"] == "lithobench_result_files"


class _Result:
    def __init__(self, text: str):
        self.text = text
        self.usage_input = 0
        self.usage_output = 0
        self.model = "mock"


class _ExperimentAwareMock:
    def chat(self, system, user, temperature=None, max_tokens=None, stop=None):
        if "Update the current paper using newly completed" in user:
            return _Result(
                "===== BEGIN FILE: 4_experiments.tex =====\n"
                "\\section{Experiments}\nNew EPE evidence from EXP-1.\n"
                "===== END FILE: 4_experiments.tex ====="
            )
        if "You are reviewing an academic paper" in user:
            return _Result(json.dumps({
                "score": 7.0,
                "summary": "Evidence is now sufficient.",
                "strengths": ["traceable evidence"],
                "weaknesses": [],
                "verdict": "ready",
            }))
        return _Result("Draft section without fabricated metrics.")

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        if "core claims" in user:
            return {
                "one_sentence_contribution": "Dual history improves ILT.",
                "title": "Dual-History ILT",
                "claims": [{
                    "claim": "Dual history improves EPE",
                    "evidence": "paired ablation",
                    "status": "needs_experiment",
                    "section": "4",
                }],
                "key_weaknesses": ["ablation missing"],
                "suggested_framing": "controlled empirical study",
            }
        if "building the paper outline" in user:
            return {
                "paper_type": "empirical",
                "sections": [
                    {"id": "0", "title": "Abstract", "filename": "0_abstract.tex"},
                    {"id": "1", "title": "Introduction", "filename": "1_introduction.tex"},
                    {"id": "3", "title": "Method", "filename": "3_method.tex"},
                    {"id": "4", "title": "Experiments", "filename": "4_experiments.tex"},
                    {"id": "5", "title": "Conclusion", "filename": "5_conclusion.tex"},
                ],
                "figure_plan": [],
                "citation_plan": {},
            }
        if "Run the final scientific writing quality audit" in user:
            return {"issues": [], "passes_clean": [True] * 13, "overall": "clean"}
        return {}

    def chat_json_list(self, system, user, temperature=None, max_tokens=None):
        return []


def test_pipeline_pauses_and_resumes_with_experiment_evidence(tmp_path: Path):
    cfg = load_config()
    cfg.data["pipeline"]["output_dir"] = str(tmp_path / "out")
    cfg.data["write"]["dblp_verify"] = False
    cfg.data["review"]["max_rounds"] = 1
    paper_dir = tmp_path / "out" / "paper"
    client = _ExperimentAwareMock()
    pipeline = Pipeline(cfg, verbose=False, client=client)  # type: ignore[arg-type]

    first = pipeline.run(
        source="Dual-history ILT study",
        experiment_bundle=_bundle(tmp_path / "evidence.json"),
    )
    assert first["status"] == "waiting_for_experiment"
    request = ExperimentExchange(paper_dir).requests()[0]
    assert (paper_dir / "sections" / "4_experiments.tex").exists()

    ExperimentExchange(paper_dir).append_response({
        "request_id": request["request_id"],
        "status": "COMPLETED",
        "experiment_ids": ["EXP-1"],
        "metrics": {"EPE": 5.5},
        "decision": "KEEP_AS_BEST",
    })
    resumed = pipeline.resume(paper_dir)
    assert resumed["status"] == "completed"
    assert resumed["review"]["stopped_reason"] == "positive_assessment"
    assert "EXP-1" in (paper_dir / "sections" / "4_experiments.tex").read_text(encoding="utf-8")


class _ReviewRequestsExperiment:
    def chat(self, system, user, temperature=None, max_tokens=None, stop=None):
        return _Result(json.dumps({
            "score": 5.0,
            "summary": "Ablation evidence is missing.",
            "strengths": ["clear method"],
            "weaknesses": [{
                "severity": "MAJOR",
                "issue": "The Dual-history contribution lacks an ablation.",
                "fix": "Run a controlled with/without comparison.",
                "location": "4_experiments.tex",
                "action": "EXPERIMENT_REQUIRED",
                "experiment_request": {
                    "type": "ABLATION",
                    "question": "Does Dual-history independently improve EPE?",
                    "hypothesis": "Removing Dual-history worsens EPE.",
                    "required_metrics": ["EPE", "L2", "PVB"],
                },
            }],
            "verdict": "not ready",
        }))


def test_review_loop_emits_experiment_request(tmp_path: Path):
    paper_dir = tmp_path / "paper"
    sections = paper_dir / "sections"
    sections.mkdir(parents=True)
    (sections / "4_experiments.tex").write_text(
        "\\section{Experiments}\nCurrent results.\n", encoding="utf-8"
    )
    cfg = load_config()
    cfg.data["review"]["max_rounds"] = 1
    exchange = ExperimentExchange(paper_dir)
    result = run_review_loop(
        _ReviewRequestsExperiment(),  # type: ignore[arg-type]
        cfg,
        paper_dir,
        max_rounds=1,
        experiment_exchange=exchange,
    )
    assert result.stopped_reason == "experiment_required"
    assert result.experiment_requests[0]["type"] == "ABLATION"
    assert exchange.requests()[0]["origin"]["phase"] == "review"


def test_not_ready_verdict_is_not_normalized_to_ready():
    parsed = _parse_review_json(json.dumps({
        "score": 5,
        "weaknesses": [],
        "verdict": "not ready",
    }))
    assert parsed["verdict"] == "not ready"
