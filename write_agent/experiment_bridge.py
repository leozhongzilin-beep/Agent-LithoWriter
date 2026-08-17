"""File-based bridge between the writing agent and an experiment loop.

The first integration version deliberately uses JSON/JSONL files instead of
requiring a long-running service.  This keeps long GPU experiments resumable
and leaves execution, approval, and rollback under the experiment loop's
control.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
TERMINAL_RESPONSE_STATUSES = {"COMPLETED", "FAILED", "REJECTED", "CANCELLED"}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(item, dict):
            records.append(item)
    return records


@dataclass(frozen=True)
class EvidenceBundle:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceBundle":
        bundle_path = Path(path).resolve()
        data = _read_json(bundle_path)
        version = str(data.get("schema_version", ""))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported evidence bundle schema_version={version!r}; "
                f"expected {SCHEMA_VERSION!r}"
            )
        if not data.get("goal_id"):
            raise ValueError("Evidence bundle must contain goal_id")
        return cls(path=bundle_path, data=data)

    def prompt_context(self, max_chars: int = 30000) -> str:
        """Render a bounded, machine-faithful prompt block.

        The JSON is kept verbatim so the model sees exact metric values and
        provenance.  Exporters should summarize very large raw artifacts and
        retain their URIs instead of embedding them here.
        """
        text = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [bundle truncated; use artifact URIs for details]"
        return (
            "STRUCTURED EXPERIMENT EVIDENCE (authoritative; never invent missing values):\n"
            + text
        )


@dataclass
class WritingState:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, paper_dir: Path) -> "WritingState":
        path = paper_dir / "WRITING_STATE.json"
        data = _read_json(path) if path.exists() else {}
        return cls(path=path, data=data)

    def save(self, status: str, **updates: Any) -> None:
        self.data.update(updates)
        self.data["schema_version"] = SCHEMA_VERSION
        self.data["status"] = status
        self.data["updated_at"] = utcnow_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class ExperimentExchange:
    """Append-only request/response exchange stored beside the paper."""

    def __init__(self, paper_dir: Path):
        self.paper_dir = paper_dir
        self.requests_path = paper_dir / "EXPERIMENT_REQUESTS.jsonl"
        self.responses_path = paper_dir / "EXPERIMENT_RESPONSES.jsonl"

    def requests(self) -> list[dict[str, Any]]:
        return read_jsonl(self.requests_path)

    def responses(self) -> list[dict[str, Any]]:
        return read_jsonl(self.responses_path)

    def append_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(request)
        existing_records = self.requests()
        existing = {str(r.get("request_id")) for r in existing_records}
        request_id = str(request["request_id"])
        if request_id in existing:
            latest = self.latest_responses().get(request_id)
            if not latest or latest.get("status") not in TERMINAL_RESPONSE_STATUSES:
                return next(r for r in existing_records if r.get("request_id") == request_id)
            # The reviewer may request the same scientific test again after a
            # completed response proved insufficient. Preserve both cycles.
            revision = 2
            while f"{request_id}-R{revision}" in existing:
                revision += 1
            request["supersedes_request_id"] = request_id
            request["request_id"] = f"{request_id}-R{revision}"
            request["created_at"] = utcnow_iso()
        _append_jsonl(self.requests_path, request)
        return request

    def append_response(self, response: dict[str, Any]) -> dict[str, Any]:
        response = normalize_response(response)
        _append_jsonl(self.responses_path, response)
        return response

    def latest_responses(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for response in self.responses():
            request_id = str(response.get("request_id", ""))
            if request_id:
                latest[request_id] = response
        return latest

    def unresolved(self, request_ids: Iterable[str]) -> list[str]:
        latest = self.latest_responses()
        unresolved: list[str] = []
        for request_id in request_ids:
            response = latest.get(request_id)
            if not response or response.get("status") not in TERMINAL_RESPONSE_STATUSES:
                unresolved.append(request_id)
        return unresolved

    def completed_for(self, request_ids: Iterable[str]) -> list[dict[str, Any]]:
        latest = self.latest_responses()
        return [
            latest[request_id]
            for request_id in request_ids
            if request_id in latest and latest[request_id].get("status") == "COMPLETED"
        ]


def _stable_request_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12].upper()
    return f"WR-EXP-{digest}"


def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    data = dict(request)
    # Review models occasionally use human-friendly keys even when the prompt
    # asks for the strict bridge contract. Preserve that information and map
    # it into executable fields before defaults can hide the omission.
    if data.get("experiment") and not data.get("protocol_summary"):
        data["protocol_summary"] = data["experiment"]
    if data.get("success_metric") and not data.get("success_condition"):
        data["success_condition"] = data["success_metric"]
    if isinstance(data.get("budget"), str):
        data.setdefault("budget_description", data["budget"])
        gpu_match = re.search(
            r"(?:approximately|about|~)?\s*([0-9]+(?:\.[0-9]+)?)\s*GPU[- ]?hours?",
            data["budget"],
            flags=re.IGNORECASE,
        )
        if gpu_match:
            data.setdefault("estimated_gpu_hours", float(gpu_match.group(1)))

    combined_text = "\n".join(
        str(data.get(key, ""))
        for key in ("experiment", "protocol_summary", "question", "hypothesis", "success_metric")
    )
    if not data.get("seeds"):
        data["seeds"] = _extract_seed_values(combined_text)
    if not data.get("required_metrics"):
        data["required_metrics"] = _infer_required_metrics(combined_text)
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("status", "PENDING")
    data.setdefault("created_at", utcnow_iso())
    data.setdefault("type", "CONFIRMATION_RUN")
    data.setdefault("origin", {})
    data.setdefault("variables", {})
    data.setdefault("controlled_variables", [])
    data.setdefault("required_metrics", [])
    data.setdefault("seeds", [])
    data.setdefault("requested_artifacts", [])
    data.setdefault("execution", {})
    data.setdefault("priority", "MEDIUM")
    if not data.get("question"):
        raise ValueError("Experiment request must contain question")
    identity = {
        "type": data["type"],
        "origin": data["origin"],
        "question": data["question"],
        "hypothesis": data.get("hypothesis", ""),
    }
    data.setdefault("request_id", _stable_request_id(identity))
    return data


def _extract_seed_values(text: str) -> list[int]:
    """Extract bounded seed lists/ranges without treating every year as a seed."""
    values: set[int] = set()
    for match in re.finditer(r"(\d{3,10})\s*[-\u2013\u2014]\s*(\d{3,10})", text):
        context = text[max(0, match.start() - 32):match.end() + 8].lower()
        if "seed" not in context:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        if 0 <= end - start <= 32:
            values.update(range(start, end + 1))
    for match in re.finditer(r"seeds?\s*(?:=|:|\{|\[|\()?\s*([0-9, /]+)", text, re.IGNORECASE):
        values.update(int(token) for token in re.findall(r"\d{3,10}", match.group(1)))
    return sorted(values)


def _infer_required_metrics(text: str) -> list[str]:
    lowered = text.lower()
    mapping = (
        ("score", "normalized_score"),
        ("l2", "official_l2"),
        ("pvb", "official_pvb"),
        ("epe", "official_epe"),
        ("runtime", "runtime_seconds"),
    )
    return [canonical for token, canonical in mapping if token in lowered]


def normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    data = dict(response)
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("created_at", utcnow_iso())
    data.setdefault("status", "COMPLETED")
    data.setdefault("experiment_ids", [])
    data.setdefault("metrics", {})
    data.setdefault("aggregate_results", {})
    data.setdefault("artifacts", [])
    data.setdefault("limitations", [])
    if not data.get("request_id"):
        raise ValueError("Experiment response must contain request_id")
    return data


def requests_from_missing_claims(claims: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, claim in enumerate(claims, 1):
        if str(claim.get("status", "")).lower() != "needs_experiment":
            continue
        claim_text = str(claim.get("claim", "")).strip()
        evidence = str(claim.get("evidence", "")).strip()
        request = normalize_request({
            "type": "CONFIRMATION_RUN",
            "origin": {
                "phase": "planning",
                "section": str(claim.get("section", "")),
                "claim_id": f"C{index}",
            },
            "question": f"What experiment is required to support or falsify this claim: {claim_text}",
            "hypothesis": claim_text,
            "required_evidence": evidence,
            "success_condition": f"Produce traceable evidence sufficient to evaluate claim C{index}",
            "priority": "HIGH",
        })
        requests.append(request)
    return requests


def request_from_review_weakness(weakness: dict[str, Any], round_num: int) -> dict[str, Any]:
    spec = weakness.get("experiment_request")
    data = dict(spec) if isinstance(spec, dict) else {}
    data.setdefault("type", "ABLATION")
    data.setdefault("origin", {
        "phase": "review",
        "round": round_num,
        "section": str(weakness.get("location", "")),
    })
    data.setdefault("question", str(weakness.get("issue", "")).strip())
    data.setdefault("hypothesis", str(weakness.get("fix", "")).strip())
    data.setdefault("priority", "HIGH" if str(weakness.get("severity", "")).upper() == "CRITICAL" else "MEDIUM")
    return normalize_request(data)


def evidence_update_context(
    bundle: EvidenceBundle | None,
    responses: Iterable[dict[str, Any]],
    requests: Iterable[dict[str, Any]] = (),
) -> str:
    parts: list[str] = []
    response_list = list(responses)
    requests_by_id = {
        str(request.get("request_id", "")): request
        for request in requests
        if request.get("request_id")
    }
    if response_list:
        direction = ""
        if bundle is not None and isinstance(bundle.data.get("score"), dict):
            direction = str(bundle.data["score"].get("direction", ""))
        handoffs: list[dict[str, Any]] = []
        for response in response_list:
            request_id = str(response.get("request_id", ""))
            comparison = response.get("comparison", {})
            interpretation: dict[str, Any] = {"score_direction": direction}
            if isinstance(comparison, dict):
                target = comparison.get("target_normalized_score_mean")
                reference = comparison.get("reference_normalized_score_mean")
                if isinstance(target, (int, float)) and isinstance(reference, (int, float)):
                    delta = abs(float(target) - float(reference))
                    if direction == "minimize":
                        winner = "target" if target < reference else "reference"
                    elif direction == "maximize":
                        winner = "target" if target > reference else "reference"
                    else:
                        winner = "undetermined"
                    interpretation.update({
                        "target_mean": target,
                        "reference_mean": reference,
                        "absolute_difference": delta,
                        "better_side_under_score_direction": winner,
                        "plain_language": (
                            f"Under score direction '{direction}', {winner} is better by "
                            f"{delta:.6g}. Reference mean={float(reference):.10g}; "
                            f"target mean={float(target):.10g}. Explicitly state which mean is "
                            "lower/higher and do not describe the other side as better."
                            if winner != "undetermined"
                            else "Score direction is unavailable; do not infer which side is better"
                        ),
                    })
                interpretation["claim_supported"] = comparison.get("claim_supported")
                interpretation["threshold"] = comparison.get("threshold")
            handoffs.append({
                "request": requests_by_id.get(request_id, {"request_id": request_id}),
                "response": response,
                "mandatory_interpretation": interpretation,
            })
        parts.append(
            "NEW REQUEST-RESPONSE HANDOFFS (highest priority; authoritative):\n"
            + json.dumps(handoffs, ensure_ascii=False, indent=2, sort_keys=True)
        )
    if bundle is not None:
        # The resume task is about the new response.  Keep historical context
        # secondary and compact so it cannot bury the evidence that triggered
        # the resume.
        parts.append(bundle.prompt_context(max_chars=12000))
    return "\n\n".join(parts)
