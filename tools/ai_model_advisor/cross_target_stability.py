from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean, median
from typing import Any

from .cross_target_experiment import cross_target_profiles
from .experiment_run import ExperimentRunnerError, find_experiment
from .feedback import FeedbackStore, UsageRecord

_DEFAULT_MIN_PAIRS = 3
_DEFAULT_MAX_MAD = 0.25
_DEFAULT_MAX_FAILURE_DELTA = 0.20
_NEUTRAL_OVERHEAD_BAND = 0.05


def _config_key(config: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(config.get("model_id") or ""),
        str(config.get("effort") or ""),
        str(config.get("execution_mode") or ""),
    )


def _record_key(record: UsageRecord) -> tuple[str, str, str]:
    return record.model_id, record.effort, record.execution_mode


def _mad(values: list[float]) -> float | None:
    if not values:
        return None
    center = median(values)
    return median(abs(value - center) for value in values)


def _ratio_delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a <= 0:
        return None
    return (b - a) / a


def _direction(value: float | None) -> str:
    if value is None or abs(value) <= _NEUTRAL_OVERHEAD_BAND:
        return "neutral"
    return "b_slower_or_costlier" if value > 0 else "b_faster_or_cheaper"


def _side_records(
    feedback: FeedbackStore,
    experiment_id: str,
    side: str,
    config_key: tuple[str, str, str],
    task_category: str,
) -> dict[str, UsageRecord]:
    suffix = side.lower()
    result: dict[str, UsageRecord] = {}
    for record in feedback.records:
        if not record.task_id:
            continue
        if _record_key(record) != config_key:
            continue
        if record.task_category != task_category:
            continue
        expected_source = f"benchmark:{experiment_id}:{record.task_id}:{suffix}"
        if record.source_id != expected_source:
            continue
        if record.task_id in result:
            raise ExperimentRunnerError(
                f"Duplicate cross-target feedback for side {side} task_id={record.task_id!r}"
            )
        result[record.task_id] = record
    return result


def build_cross_target_stability_report(
    plan: dict[str, Any],
    feedback: FeedbackStore,
    experiment_id: str,
    *,
    min_pairs: int = _DEFAULT_MIN_PAIRS,
    max_mad: float = _DEFAULT_MAX_MAD,
    max_failure_rate_delta: float = _DEFAULT_MAX_FAILURE_DELTA,
) -> dict[str, Any]:
    if min_pairs < 2:
        raise ExperimentRunnerError("min_pairs must be >= 2")
    if not 0 <= max_mad <= 1:
        raise ExperimentRunnerError("max_mad must be between 0 and 1")
    if not 0 <= max_failure_rate_delta <= 1:
        raise ExperimentRunnerError("max_failure_rate_delta must be between 0 and 1")

    profiles = cross_target_profiles(plan)
    pair = find_experiment(plan, experiment_id)
    if pair.get("kind") != "execution_target_overhead":
        raise ExperimentRunnerError(
            "Cross-target stability requires kind='execution_target_overhead'"
        )
    task_category = str(pair.get("category") or "")
    if not task_category:
        raise ExperimentRunnerError("Cross-target experiment is missing category")
    primary = pair["primary"]
    challenger = pair["challenger"]
    if not isinstance(primary, dict) or not isinstance(challenger, dict):
        raise ExperimentRunnerError("Cross-target experiment configurations must be objects")

    a_key = _config_key(primary)
    b_key = _config_key(challenger)
    if not all(a_key) or not all(b_key):
        raise ExperimentRunnerError("Cross-target experiment is missing model/effort/execution identity")

    side_a = _side_records(feedback, experiment_id, "A", a_key, task_category)
    side_b = _side_records(feedback, experiment_id, "B", b_key, task_category)
    matched_ids = sorted(set(side_a) & set(side_b))

    latency_deltas: list[float] = []
    cost_deltas: list[float] = []
    pair_rows: list[dict[str, Any]] = []
    a_failures = 0
    b_failures = 0

    for task_id in matched_ids:
        a = side_a[task_id]
        b = side_b[task_id]
        if a.outcome == "failure":
            a_failures += 1
        if b.outcome == "failure":
            b_failures += 1

        latency_delta = _ratio_delta(a.latency_seconds, b.latency_seconds)
        cost_delta = _ratio_delta(a.cost_usd, b.cost_usd)
        if a.outcome == "success" and b.outcome == "success":
            if latency_delta is not None:
                latency_deltas.append(latency_delta)
            if cost_delta is not None:
                cost_deltas.append(cost_delta)

        pair_rows.append(
            {
                "task_id": task_id,
                "a_outcome": a.outcome,
                "b_outcome": b.outcome,
                "latency_overhead_ratio": latency_delta,
                "cost_overhead_ratio": cost_delta,
            }
        )

    matched = len(matched_ids)
    a_failure_rate = a_failures / matched if matched else None
    b_failure_rate = b_failures / matched if matched else None
    failure_delta = (
        b_failure_rate - a_failure_rate
        if a_failure_rate is not None and b_failure_rate is not None
        else None
    )

    latency_median = median(latency_deltas) if latency_deltas else None
    cost_median = median(cost_deltas) if cost_deltas else None
    latency_mad = _mad(latency_deltas)
    cost_mad = _mad(cost_deltas)

    blockers: list[str] = []
    if matched < min_pairs:
        blockers.append(f"matched_pairs<{min_pairs}")
    if len(latency_deltas) < min_pairs:
        blockers.append(f"successful_latency_pairs<{min_pairs}")
    if latency_mad is not None and latency_mad > max_mad:
        blockers.append("latency_overhead_unstable")
    if cost_mad is not None and len(cost_deltas) >= min_pairs and cost_mad > max_mad:
        blockers.append("cost_overhead_unstable")
    if failure_delta is not None and abs(failure_delta) > max_failure_rate_delta:
        blockers.append("failure_rate_delta_too_large")

    if matched < min_pairs or len(latency_deltas) < min_pairs:
        status = "insufficient_evidence"
    elif any(
        blocker
        in {
            "latency_overhead_unstable",
            "cost_overhead_unstable",
            "failure_rate_delta_too_large",
        }
        for blocker in blockers
    ):
        status = "unstable"
    else:
        status = "stable_overhead"

    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "category": pair["category"],
        "kind": pair["kind"],
        "status": status,
        "side_a_target": profiles["A"].host,
        "side_b_target": profiles["B"].host,
        "matched_pairs": matched,
        "successful_latency_pairs": len(latency_deltas),
        "successful_cost_pairs": len(cost_deltas),
        "latency": {
            "median_overhead_ratio": latency_median,
            "mean_overhead_ratio": fmean(latency_deltas) if latency_deltas else None,
            "mad": latency_mad,
            "direction": _direction(latency_median),
        },
        "cost": {
            "median_overhead_ratio": cost_median,
            "mean_overhead_ratio": fmean(cost_deltas) if cost_deltas else None,
            "mad": cost_mad,
            "direction": _direction(cost_median),
        },
        "failure_rate": {
            "side_a": a_failure_rate,
            "side_b": b_failure_rate,
            "delta_b_minus_a": failure_delta,
        },
        "policy": {
            "min_pairs": min_pairs,
            "max_mad": max_mad,
            "max_failure_rate_delta": max_failure_rate_delta,
            "automatic_routing_mutation": False,
        },
        "blockers": blockers,
        "pairs": pair_rows,
    }


def cross_target_stability_markdown(report: dict[str, Any]) -> str:
    latency = report["latency"]
    cost = report["cost"]
    failure = report["failure_rate"]

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value * 100:+.1f}%"

    lines = [
        "# AI Model Advisor Cross-Target Stability",
        "",
        f"Experiment: `{report['experiment_id']}`",
        f"Status: **{report['status']}**",
        f"Matched pairs: **{report['matched_pairs']}**",
        f"Targets: `{report['side_a_target']}` → `{report['side_b_target']}`",
        "",
        "| Metric | Median/Delta | Mean | MAD |",
        "|---|---:|---:|---:|",
        (
            f"| Latency overhead | {pct(latency['median_overhead_ratio'])} | "
            f"{pct(latency['mean_overhead_ratio'])} | {pct(latency['mad'])} |"
        ),
        (
            f"| Cost overhead | {pct(cost['median_overhead_ratio'])} | "
            f"{pct(cost['mean_overhead_ratio'])} | {pct(cost['mad'])} |"
        ),
        (
            f"| Failure-rate delta (B-A) | {pct(failure['delta_b_minus_a'])} | — | — |"
        ),
        "",
        "This report is analytical only. It never mutates routing or Hive configuration.",
        "",
    ]
    if report["blockers"]:
        lines.extend(["Blockers:"] + [f"- `{item}`" for item in report["blockers"]] + [""])
    return "\n".join(lines)


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExperimentRunnerError("plan root must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate stability of matched cross-target AgentLoop overhead evidence.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--feedback", required=True)
    parser.add_argument("--min-pairs", type=int, default=_DEFAULT_MIN_PAIRS)
    parser.add_argument("--max-mad", type=float, default=_DEFAULT_MAX_MAD)
    parser.add_argument("--max-failure-rate-delta", type=float, default=_DEFAULT_MAX_FAILURE_DELTA)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    try:
        report = build_cross_target_stability_report(
            _load_json(args.plan),
            FeedbackStore.load(args.feedback),
            args.experiment_id,
            min_pairs=args.min_pairs,
            max_mad=args.max_mad,
            max_failure_rate_delta=args.max_failure_rate_delta,
        )
    except (OSError, ValueError, json.JSONDecodeError, ExperimentRunnerError) as exc:
        print(f"cross-target stability error: {exc}")
        return 2

    markdown = cross_target_stability_markdown(report)
    if args.output:
        Path(args.output).write_text(markdown + "\n", encoding="utf-8")
    else:
        print(markdown)
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
