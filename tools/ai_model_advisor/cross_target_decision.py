from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_DEFAULT_MAX_LATENCY_OVERHEAD = 0.15
_DEFAULT_MAX_COST_OVERHEAD = 0.15
_DEFAULT_MATERIAL_RELIABILITY_GAIN = 0.10
_ALLOWED_STABILITY_STATES = {
    "insufficient_evidence",
    "unstable",
    "stable_overhead",
}


class CrossTargetDecisionError(ValueError):
    pass


def _number_or_none(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CrossTargetDecisionError(f"{name} must be numeric or null")
    return float(value)


def _threshold(value: float, name: str) -> float:
    if value < 0 or value > 1:
        raise CrossTargetDecisionError(f"{name} must be between 0 and 1")
    return value


def build_cross_target_decision(
    stability: dict[str, Any],
    *,
    max_latency_overhead: float = _DEFAULT_MAX_LATENCY_OVERHEAD,
    max_cost_overhead: float = _DEFAULT_MAX_COST_OVERHEAD,
    material_reliability_gain: float = _DEFAULT_MATERIAL_RELIABILITY_GAIN,
) -> dict[str, Any]:
    """Turn one v0.40 stability report into a bounded operator recommendation.

    The result is intentionally advisory. It only applies to the benchmark
    class represented by the matched experiment and never mutates routing.
    """
    if not isinstance(stability, dict):
        raise CrossTargetDecisionError("stability report must be a JSON object")
    if stability.get("schema_version") != 1:
        raise CrossTargetDecisionError("unsupported stability schema_version")

    state = str(stability.get("status") or "")
    if state not in _ALLOWED_STABILITY_STATES:
        raise CrossTargetDecisionError(f"unsupported stability status: {state!r}")

    side_a = str(stability.get("side_a_target") or "")
    side_b = str(stability.get("side_b_target") or "")
    if (side_a, side_b) != ("hive_agent_loop", "hive_agent_loop_tool"):
        raise CrossTargetDecisionError(
            "cross-target decision requires hive_agent_loop -> hive_agent_loop_tool"
        )

    max_latency_overhead = _threshold(max_latency_overhead, "max_latency_overhead")
    max_cost_overhead = _threshold(max_cost_overhead, "max_cost_overhead")
    material_reliability_gain = _threshold(
        material_reliability_gain,
        "material_reliability_gain",
    )

    latency = stability.get("latency")
    cost = stability.get("cost")
    failure = stability.get("failure_rate")
    if not isinstance(latency, dict) or not isinstance(cost, dict) or not isinstance(failure, dict):
        raise CrossTargetDecisionError("stability report is missing metric blocks")

    latency_overhead = _number_or_none(
        latency.get("median_overhead_ratio"),
        "latency.median_overhead_ratio",
    )
    cost_overhead = _number_or_none(
        cost.get("median_overhead_ratio"),
        "cost.median_overhead_ratio",
    )
    failure_delta = _number_or_none(
        failure.get("delta_b_minus_a"),
        "failure_rate.delta_b_minus_a",
    )

    if state == "insufficient_evidence":
        decision = "collect_more"
        rationale = (
            "The matched experiment does not yet have enough successful repeated "
            "pairs to make an execution-target choice."
        )
    elif state == "unstable":
        decision = "investigate_instability"
        rationale = (
            "Enough data exists to measure the comparison, but the overhead or "
            "failure-rate signal is not stable enough to use for target selection."
        )
    else:
        latency_exceeds = (
            latency_overhead is not None and latency_overhead > max_latency_overhead
        )
        cost_exceeds = cost_overhead is not None and cost_overhead > max_cost_overhead
        tool_reliability_gain = (
            failure_delta is not None
            and failure_delta <= -material_reliability_gain
        )

        if (latency_exceeds or cost_exceeds) and tool_reliability_gain:
            decision = "manual_tradeoff_review"
            rationale = (
                "The tool-enabled target has material overhead but also a material "
                "failure-rate advantage. Preserve the tradeoff for human review."
            )
        elif latency_exceeds or cost_exceeds:
            decision = "prefer_no_tool_for_equivalent_tasks"
            rationale = (
                "The tool-enabled target has stable material overhead without a "
                "material reliability advantage on this benchmark class."
            )
        else:
            decision = "tool_overhead_acceptable"
            rationale = (
                "The tool-enabled target stayed within the configured stable "
                "latency/cost overhead budget for this benchmark class."
            )

    return {
        "schema_version": 1,
        "source_stability_schema_version": stability["schema_version"],
        "experiment_id": stability.get("experiment_id"),
        "category": stability.get("category"),
        "side_a_target": side_a,
        "side_b_target": side_b,
        "stability_status": state,
        "decision": decision,
        "rationale": rationale,
        "observed": {
            "median_latency_overhead_ratio": latency_overhead,
            "median_cost_overhead_ratio": cost_overhead,
            "failure_rate_delta_b_minus_a": failure_delta,
            "matched_pairs": stability.get("matched_pairs"),
        },
        "policy": {
            "max_latency_overhead": max_latency_overhead,
            "max_cost_overhead": max_cost_overhead,
            "material_reliability_gain": material_reliability_gain,
            "scope": "equivalent_benchmark_tasks_only",
            "safe_to_auto_apply": False,
            "automatic_routing_mutation": False,
            "automatic_hive_config_mutation": False,
        },
    }


def cross_target_decision_markdown(report: dict[str, Any]) -> str:
    observed = report["observed"]

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value * 100:+.1f}%"

    return "\n".join(
        [
            "# AI Model Advisor Cross-Target Decision",
            "",
            f"Experiment: `{report['experiment_id']}`",
            f"Category: **{report['category']}**",
            f"Stability: **{report['stability_status']}**",
            f"Decision: **{report['decision']}**",
            "",
            f"- Median latency overhead: {pct(observed['median_latency_overhead_ratio'])}",
            f"- Median cost overhead: {pct(observed['median_cost_overhead_ratio'])}",
            f"- Failure-rate delta (tool - no-tool): {pct(observed['failure_rate_delta_b_minus_a'])}",
            f"- Matched pairs: {observed['matched_pairs']}",
            "",
            report["rationale"],
            "",
            (
                "Scope: equivalent benchmark tasks only. This report is advisory "
                "and never mutates routing or Hive configuration."
            ),
            "",
        ]
    )


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CrossTargetDecisionError("stability report root must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn cross-target stability evidence into a bounded operator decision."
    )
    parser.add_argument("--stability", required=True)
    parser.add_argument(
        "--max-latency-overhead",
        type=float,
        default=_DEFAULT_MAX_LATENCY_OVERHEAD,
    )
    parser.add_argument(
        "--max-cost-overhead",
        type=float,
        default=_DEFAULT_MAX_COST_OVERHEAD,
    )
    parser.add_argument(
        "--material-reliability-gain",
        type=float,
        default=_DEFAULT_MATERIAL_RELIABILITY_GAIN,
    )
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    try:
        report = build_cross_target_decision(
            _load_json(args.stability),
            max_latency_overhead=args.max_latency_overhead,
            max_cost_overhead=args.max_cost_overhead,
            material_reliability_gain=args.material_reliability_gain,
        )
    except (OSError, json.JSONDecodeError, CrossTargetDecisionError) as exc:
        print(f"cross-target decision error: {exc}")
        return 2

    markdown = cross_target_decision_markdown(report)
    if args.output:
        Path(args.output).write_text(markdown + "\n", encoding="utf-8")
    else:
        print(markdown)
    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
