"""Automatic hand-off from a paused paper to model-optimize-loop."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Config


def dispatch_requests(
    config: Config,
    paper_dir: Path,
    requests: Iterable[dict[str, Any]],
    *,
    experiment_bundle: Path | None,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Submit unresolved requests and return the loop acknowledgement records.

    Submission is short-lived. model-optimize-loop launches its worker through
    ``Runner.launch_detached``; that worker owns the GPU campaign, result
    ingestion, response append, and writing-agent resume.
    """
    loop_root = config.auto_loop_root
    workspace_root = config.auto_workspace_root
    project_profile = config.auto_project_profile
    if loop_root is None or workspace_root is None:
        raise ValueError(
            "Automatic experiments require --loop-root and --workspace-root "
            "(--lithobench-root remains a compatible alias) "
            "(or the matching WRITING_AGENT_* environment variables)."
        )
    loop_root = loop_root.resolve()
    workspace_root = workspace_root.resolve()
    if project_profile is not None:
        project_profile = project_profile.resolve()
    if not (loop_root / "orchestration").exists():
        raise ValueError(f"model-optimize-loop root is invalid: {loop_root}")
    if not workspace_root.exists():
        raise ValueError(f"Experiment workspace does not exist: {workspace_root}")
    if project_profile is not None and not project_profile.is_file():
        raise ValueError(f"Project profile does not exist: {project_profile}")
    if project_profile is None and not (
        workspace_root / "pw_lpd_ilt" / "run_experiment.py"
    ).exists():
        raise ValueError(
            "Legacy LPD-ILT mode requires pw_lpd_ilt/run_experiment.py. "
            "For another member or method, provide --project-profile."
        )

    submission_dir = paper_dir / "AUTO_EXPERIMENT_REQUESTS"
    submission_dir.mkdir(parents=True, exist_ok=True)
    acknowledgements: list[dict[str, Any]] = []
    for request in requests:
        request_id = str(request.get("request_id", "")).strip()
        if not request_id:
            continue
        request_path = submission_dir / f"{request_id}.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        command = [
            sys.executable,
            "-m",
            "orchestration.writing_bridge",
            "submit",
            "--request-file",
            str(request_path.resolve()),
            "--paper-dir",
            str(paper_dir.resolve()),
            "--writing-root",
            str(Path(__file__).resolve().parents[1]),
            "--workspace-root",
            str(workspace_root),
            "--experiment-python",
            str(config.auto_experiment_python),
            "--writing-python",
            sys.executable,
            "--auto-resume",
        ]
        if project_profile is not None:
            command += ["--project-profile", str(project_profile)]
        if experiment_bundle is not None:
            command += ["--experiment-bundle", str(experiment_bundle.resolve())]
        completed = subprocess.run(
            command,
            cwd=str(loop_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Loop rejected {request_id}: {detail}")
        try:
            ack = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Loop returned an invalid acknowledgement for {request_id}: "
                f"{completed.stdout[-1000:]}"
            ) from exc
        acknowledgements.append(ack)
        log(
            f"[integration] Loop accepted {request_id}: "
            f"status={ack.get('status')} pid={ack.get('pid')}"
        )
        log(f"[integration] Loop job state: {ack.get('state_path')}")
    return acknowledgements
