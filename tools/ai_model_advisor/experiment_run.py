from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .feedback import EXACT_FEEDBACK_MIN, FeedbackStore, UsageRecord

RunnerExecutor = Callable[[dict[str, Any]], dict[str, Any]]
_VALID_OUTCOMES = {"success", "partial", "failure"}
_CONFIG_KEYS = ("provider", "model_id", "effort", "execution_mode")
_RUNNER_SCHEMA_VERSION = 1


class ExperimentRunnerError(ValueError):
    """Raised when a saved experiment or caller input is unsafe to use."""


class RunnerInfrastructureError(RuntimeError):
    """Raised when the execution adapter fails before producing trustworthy evidence."""


@dataclass(frozen=True)
class PairRunReport:
    experiment_id: str
    category: str
    kind: str
    task_id: str
    task_sha256: str
    order: tuple[str, str]
    records: tuple[UsageRecord, UsageRecord]

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "category": self.category,
            "kind": self.kind,
            "task_id": self.task_id,
            "task_sha256": self.task_sha256,
            "order": list(self.order),
            "records": [record.as_dict() for record in self.records],
        }


def find_experiment(plan: dict[str, Any], experiment_id: str) -> dict[str, Any]:
    matches = [
        pair
        for category in plan.get("categories", [])
        for pair in category.get("pairs", [])
        if pair.get("experiment_id") == experiment_id
    ]
    if len(matches) != 1:
        raise ExperimentRunnerError(
            f"Expected exactly one experiment_id={experiment_id!r}, found {len(matches)}"
        )
    pair = matches[0]
    for key in ("category", "kind", "primary", "challenger", "task_id_template"):
        if key not in pair:
            raise ExperimentRunnerError(f"Saved experiment is missing {key!r}")
    return pair


def _task_prefix(pair: dict[str, Any]) -> str:
    template = str(pair["task_id_template"])
    marker = "{nn}"
    if marker not in template:
        raise ExperimentRunnerError("task_id_template must contain {nn}")
    return template.split(marker, 1)[0]


def next_task_id(pair: dict[str, Any], feedback: FeedbackStore) -> tuple[str, int]:
    prefix = _task_prefix(pair)
    used = {record.task_id for record in feedback.records if record.task_id}
    for index in range(1, 100_000):
        candidate = f"{prefix}{index:02d}"
        if candidate not in used:
            return candidate, index
    raise ExperimentRunnerError("Could not allocate a free benchmark task ID")


def _task_index(pair: dict[str, Any], task_id: str) -> int:
    prefix = _task_prefix(pair)
    if not task_id.startswith(prefix):
        raise ExperimentRunnerError(
            f"task_id must start with the saved experiment prefix {prefix!r}"
        )
    suffix = task_id[len(prefix) :]
    if not suffix.isdigit():
        raise ExperimentRunnerError("task_id suffix must be numeric")
    return int(suffix)


def execution_order(pair: dict[str, Any], task_id: str, requested: str = "auto") -> tuple[str, str]:
    if requested == "ab":
        return ("A", "B")
    if requested == "ba":
        return ("B", "A")
    if requested != "auto":
        raise ExperimentRunnerError("order must be auto, ab, or ba")
    return ("A", "B") if _task_index(pair, task_id) % 2 else ("B", "A")


def _config_for_side(pair: dict[str, Any], side: str) -> dict[str, Any]:
    if side == "A":
        config = pair["primary"]
    elif side == "B":
        config = pair["challenger"]
    else:
        raise ExperimentRunnerError(f"Unknown experiment side: {side}")
    if not isinstance(config, dict):
        raise ExperimentRunnerError(f"Experiment side {side} must be a configuration object")
    for key in _CONFIG_KEYS:
        if not config.get(key):
            raise ExperimentRunnerError(f"Experiment side {side} is missing {key!r}")
    return {key: config[key] for key in _CONFIG_KEYS}


def _record_matches_config(record: UsageRecord, config: dict[str, Any]) -> bool:
    return (
        record.provider == config["provider"]
        and record.model_id == config["model_id"]
        and record.effort == config["effort"]
        and record.execution_mode == config["execution_mode"]
    )


def current_complete_pair_task_ids(
    pair: dict[str, Any], feedback: FeedbackStore
) -> tuple[str, ...]:
    """Return unambiguous complete A/B task IDs from live feedback.

    This intentionally recomputes progress instead of trusting the status
    embedded in a saved plan, which may be stale after later benchmark runs.
    """
    prefix = _task_prefix(pair)
    category = str(pair["category"])
    primary = _config_for_side(pair, "A")
    challenger = _config_for_side(pair, "B")
    by_task: dict[str, dict[str, list[UsageRecord]]] = {}

    for record in feedback.records:
        if record.task_category != category or not record.task_id:
            continue
        if not record.task_id.startswith(prefix):
            continue
        side = None
        if _record_matches_config(record, primary):
            side = "A"
        elif _record_matches_config(record, challenger):
            side = "B"
        if side is None:
            continue
        task = by_task.setdefault(record.task_id, {"A": [], "B": []})
        task[side].append(record)

    return tuple(
        sorted(
            task_id
            for task_id, sides in by_task.items()
            if len(sides["A"]) == 1 and len(sides["B"]) == 1
        )
    )


def ensure_experiment_collectable(
    pair: dict[str, Any], feedback: FeedbackStore, *, allow_ready: bool = False
) -> tuple[str, ...]:
    complete_task_ids = current_complete_pair_task_ids(pair, feedback)
    ready = pair.get("status") == "ready" or len(complete_task_ids) >= EXACT_FEEDBACK_MIN
    if ready and not allow_ready:
        raise ExperimentRunnerError(
            "Experiment is already ready for evaluation based on saved or live evidence; "
            "use allow_ready only for deliberate extra evidence"
        )
    return complete_task_ids


def task_sha256(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


def runner_payload(
    pair: dict[str, Any],
    experiment_id: str,
    side: str,
    task_id: str,
    task: str,
) -> dict[str, Any]:
    return {
        "schema_version": _RUNNER_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "side": side,
        "category": pair["category"],
        "kind": pair["kind"],
        "task_id": task_id,
        "task": task,
        "task_sha256": task_sha256(task),
        "configuration": _config_for_side(pair, side),
    }


def _validate_adapter_identity(result: dict[str, Any], expected: dict[str, Any]) -> None:
    if result.get("schema_version") != _RUNNER_SCHEMA_VERSION:
        raise RunnerInfrastructureError(
            f"runner result schema_version must be {_RUNNER_SCHEMA_VERSION}"
        )
    applied = result.get("applied_configuration")
    if not isinstance(applied, dict):
        raise RunnerInfrastructureError(
            "runner result must echo applied_configuration before evidence can be trusted"
        )
    normalized = {key: applied.get(key) for key in _CONFIG_KEYS}
    if normalized != expected:
        raise RunnerInfrastructureError(
            "runner applied_configuration does not match the saved experiment configuration"
        )


def _optional_number(result: dict[str, Any], key: str) -> float | None:
    value = result.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerInfrastructureError(f"runner result {key} must be numeric")
    return float(value)


def _optional_int(result: dict[str, Any], key: str) -> int | None:
    value = result.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunnerInfrastructureError(f"runner result {key} must be an integer")
    return value


def result_to_record(
    pair: dict[str, Any],
    experiment_id: str,
    side: str,
    task_id: str,
    result: dict[str, Any],
    measured_latency_seconds: float | None = None,
) -> UsageRecord:
    if not isinstance(result, dict):
        raise RunnerInfrastructureError("runner result must be a JSON object")
    expected_config = _config_for_side(pair, side)
    _validate_adapter_identity(result, expected_config)

    outcome = result.get("outcome")
    if outcome not in _VALID_OUTCOMES:
        raise RunnerInfrastructureError(
            "runner result outcome must be success, partial, or failure"
        )
    retries = result.get("retries", 0)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise RunnerInfrastructureError(
            "runner result retries must be a non-negative integer"
        )

    latency = _optional_number(result, "latency_seconds")
    if latency is None:
        latency = measured_latency_seconds
    source_id = f"benchmark:{experiment_id}:{task_id}:{side.lower()}"
    return UsageRecord(
        provider=str(expected_config["provider"]),
        model_id=str(expected_config["model_id"]),
        effort=str(expected_config["effort"]),
        execution_mode=str(expected_config["execution_mode"]),
        outcome=str(outcome),
        retries=retries,
        latency_seconds=latency,
        cost_usd=_optional_number(result, "cost_usd"),
        input_tokens=_optional_int(result, "input_tokens"),
        output_tokens=_optional_int(result, "output_tokens"),
        cached_tokens=_optional_int(result, "cached_tokens"),
        cache_creation_tokens=_optional_int(result, "cache_creation_tokens"),
        credits=_optional_number(result, "credits"),
        task_category=str(pair["category"]),
        task_id=task_id,
        source_id=source_id,
        note=str(result.get("note") or ""),
    )


def run_experiment_pair(
    plan: dict[str, Any],
    feedback: FeedbackStore,
    experiment_id: str,
    task: str,
    executor: RunnerExecutor,
    *,
    task_id: str | None = None,
    order: str = "auto",
    allow_ready: bool = False,
) -> PairRunReport:
    pair = find_experiment(plan, experiment_id)
    ensure_experiment_collectable(pair, feedback, allow_ready=allow_ready)

    if task_id is None:
        task_id, _ = next_task_id(pair, feedback)
    else:
        _task_index(pair, task_id)
    run_order = execution_order(pair, task_id, order)

    expected_source_ids = {
        f"benchmark:{experiment_id}:{task_id}:a",
        f"benchmark:{experiment_id}:{task_id}:b",
    }
    duplicate_sources = expected_source_ids & feedback.source_ids
    if duplicate_sources:
        raise ExperimentRunnerError(
            "Benchmark source ID already exists: " + ", ".join(sorted(duplicate_sources))
        )

    staged: dict[str, UsageRecord] = {}
    for side in run_order:
        payload = runner_payload(pair, experiment_id, side, task_id, task)
        started = time.monotonic()
        result = executor(payload)
        elapsed = max(0.000001, time.monotonic() - started)
        staged[side] = result_to_record(
            pair,
            experiment_id,
            side,
            task_id,
            result,
            measured_latency_seconds=elapsed,
        )

    return PairRunReport(
        experiment_id=experiment_id,
        category=str(pair["category"]),
        kind=str(pair["kind"]),
        task_id=task_id,
        task_sha256=task_sha256(task),
        order=run_order,
        records=(staged["A"], staged["B"]),
    )


def command_executor(argv: Sequence[str], timeout_seconds: float) -> RunnerExecutor:
    if not argv:
        raise ExperimentRunnerError("runner command cannot be empty")
    if timeout_seconds <= 0:
        raise ExperimentRunnerError("timeout_seconds must be > 0")

    command = tuple(str(part) for part in argv)

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
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
                f"runner timed out after {timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise RunnerInfrastructureError(f"runner could not start: {exc}") from exc

        elapsed = max(0.000001, time.monotonic() - started)
        if completed.returncode != 0:
            raise RunnerInfrastructureError(
                f"runner exited with code {completed.returncode}; no model evidence was recorded"
            )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise RunnerInfrastructureError("runner returned no JSON result")
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RunnerInfrastructureError("runner's last stdout line is not valid JSON") from exc
        if not isinstance(result, dict):
            raise RunnerInfrastructureError("runner JSON result must be an object")
        result.setdefault("latency_seconds", elapsed)
        return result

    return execute


def append_pair_feedback(path: str | Path, report: PairRunReport) -> None:
    """Append both validated sides only after the full pair completed.

    A last-moment duplicate check narrows the race window between planning and
    append. This is not a cross-process lock; concurrent benchmark writers
    should still be serialized by the caller.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = FeedbackStore.load(target)
    source_ids = {record.source_id for record in report.records if record.source_id}
    duplicates = source_ids & existing.source_ids
    if duplicates:
        raise ExperimentRunnerError(
            "Benchmark source ID appeared before append: " + ", ".join(sorted(duplicates))
        )
    payload = "".join(
        json.dumps(record.as_dict(), ensure_ascii=False) + "\n" for record in report.records
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def experiment_run_markdown(report: PairRunReport, *, applied: bool) -> str:
    lines = [
        "# AI Model Advisor Experiment Run",
        "",
        f"Experiment: `{report.experiment_id}`",
        f"Category: **{report.category}**",
        f"Kind: **{report.kind}**",
        f"Task ID: `{report.task_id}`",
        f"Task SHA-256: `{report.task_sha256}`",
        f"Execution order: **{' → '.join(report.order)}**",
        f"Feedback written: **{'yes' if applied else 'no'}**",
        "",
        "| Side | Model | Effort | Execution | Outcome | Retries | Latency | Cost |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for side, record in zip(("A", "B"), report.records, strict=True):
        latency = f"{record.latency_seconds:.3f}s" if record.latency_seconds is not None else "—"
        cost = f"${record.cost_usd:.6f}" if record.cost_usd is not None else "—"
        lines.append(
            f"| {side} | `{record.model_id}` | {record.effort} | {record.execution_mode} | "
            f"{record.outcome} | {record.retries} | {latency} | {cost} |"
        )
    lines.extend(
        [
            "",
            (
                "Both sides were validated before feedback append. Adapter timeout, launch failure, "
                "non-zero exit, malformed JSON, or configuration-echo mismatch is treated as "
                "infrastructure failure rather than model failure."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def pair_run_json(report: PairRunReport, *, applied: bool) -> dict[str, Any]:
    return {
        **asdict(report),
        "records": [record.as_dict() for record in report.records],
        "applied": applied,
    }
