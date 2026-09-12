from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from .experiment_run import RunnerInfrastructureError

OutcomeJudge = Callable[[dict[str, Any]], dict[str, Any]]
_JUDGE_SCHEMA_VERSION = 1
_VALID_OUTCOMES = {"success", "partial", "failure"}


def judge_payload(
    runner_payload: dict[str, Any],
    adapter_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a checker request without changing model telemetry."""
    return {
        "schema_version": _JUDGE_SCHEMA_VERSION,
        "experiment_id": runner_payload["experiment_id"],
        "side": runner_payload["side"],
        "category": runner_payload["category"],
        "kind": runner_payload["kind"],
        "task_id": runner_payload["task_id"],
        "task": runner_payload["task"],
        "task_sha256": runner_payload["task_sha256"],
        "configuration": runner_payload["configuration"],
        "adapter_result": adapter_result,
    }


def validate_judge_result(result: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(result, dict):
        raise RunnerInfrastructureError("judge result must be a JSON object")
    if result.get("schema_version") != _JUDGE_SCHEMA_VERSION:
        raise RunnerInfrastructureError(
            f"judge result schema_version must be {_JUDGE_SCHEMA_VERSION}"
        )
    outcome = result.get("outcome")
    if outcome not in _VALID_OUTCOMES:
        raise RunnerInfrastructureError(
            "judge result outcome must be success, partial, or failure"
        )
    note = result.get("note")
    if note is not None and not isinstance(note, str):
        raise RunnerInfrastructureError("judge result note must be a string when provided")
    return str(outcome), str(note or "")


def apply_outcome_judge(
    runner_payload_data: dict[str, Any],
    adapter_result: dict[str, Any],
    judge: OutcomeJudge,
) -> dict[str, Any]:
    """Replace self-reported outcome with independently checked outcome."""
    result = judge(judge_payload(runner_payload_data, adapter_result))
    outcome, judge_note = validate_judge_result(result)

    merged = dict(adapter_result)
    adapter_note = merged.get("note")
    notes = [str(adapter_note)] if adapter_note else []
    if judge_note:
        notes.append(f"judge: {judge_note}")
    merged["outcome"] = outcome
    merged["note"] = " | ".join(notes)
    merged["outcome_source"] = "judge"
    return merged


def command_judge(argv: Sequence[str], timeout_seconds: float) -> OutcomeJudge:
    if not argv:
        raise RunnerInfrastructureError("judge command cannot be empty")
    if timeout_seconds <= 0:
        raise RunnerInfrastructureError("judge timeout_seconds must be > 0")

    command = tuple(str(part) for part in argv)

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerInfrastructureError(
                f"judge timed out after {timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise RunnerInfrastructureError(f"judge could not start: {exc}") from exc

        if completed.returncode != 0:
            raise RunnerInfrastructureError(
                f"judge exited with code {completed.returncode}; no model evidence was recorded"
            )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise RunnerInfrastructureError("judge returned no JSON result")
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RunnerInfrastructureError("judge's last stdout line is not valid JSON") from exc
        if not isinstance(result, dict):
            raise RunnerInfrastructureError("judge JSON result must be an object")
        return result

    return execute
