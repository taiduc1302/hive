from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

_ALLOWED_DECISIONS = {
    "collect_more",
    "investigate_instability",
    "prefer_no_tool_for_equivalent_tasks",
    "tool_overhead_acceptable",
    "manual_tradeoff_review",
}


class CrossTargetPortfolioError(ValueError):
    pass


def _validate_decision(decision: dict[str, Any]) -> None:
    if decision.get("schema_version") != 1:
        raise CrossTargetPortfolioError("unsupported decision schema_version")
    value = str(decision.get("decision") or "")
    if value not in _ALLOWED_DECISIONS:
        raise CrossTargetPortfolioError(f"unsupported cross-target decision: {value!r}")
    if decision.get("side_a_target") != "hive_agent_loop":
        raise CrossTargetPortfolioError("decision side_a_target must be hive_agent_loop")
    if decision.get("side_b_target") != "hive_agent_loop_tool":
        raise CrossTargetPortfolioError(
            "decision side_b_target must be hive_agent_loop_tool"
        )
    policy = decision.get("policy")
    if not isinstance(policy, dict):
        raise CrossTargetPortfolioError("decision policy must be an object")
    if policy.get("safe_to_auto_apply") is not False:
        raise CrossTargetPortfolioError("decision must be explicitly non-auto-applicable")
    if policy.get("automatic_routing_mutation") is not False:
        raise CrossTargetPortfolioError(
            "decision must disable automatic routing mutation"
        )


def build_cross_target_portfolio(
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not decisions:
        raise CrossTargetPortfolioError("at least one decision artifact is required")

    categories: set[str] = set()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for decision in decisions:
        if not isinstance(decision, dict):
            raise CrossTargetPortfolioError("each decision artifact must be an object")
        _validate_decision(decision)

        category = str(decision.get("category") or "")
        if not category:
            raise CrossTargetPortfolioError("decision category is required")
        if category in categories:
            raise CrossTargetPortfolioError(
                f"duplicate decision category is not allowed: {category!r}"
            )
        categories.add(category)

        value = str(decision["decision"])
        counts[value] += 1
        if value == "prefer_no_tool_for_equivalent_tasks":
            manual_target = "hive_agent_loop"
            operator_state = "manual_target_preference_available"
        elif value == "tool_overhead_acceptable":
            manual_target = None
            operator_state = "tool_target_within_budget"
        elif value == "manual_tradeoff_review":
            manual_target = None
            operator_state = "manual_tradeoff_required"
        elif value == "investigate_instability":
            manual_target = None
            operator_state = "investigate_before_target_choice"
        else:
            manual_target = None
            operator_state = "collect_more_before_target_choice"

        rows.append(
            {
                "category": category,
                "experiment_id": decision.get("experiment_id"),
                "decision": value,
                "operator_state": operator_state,
                "manual_target_preference": manual_target,
                "rationale": decision.get("rationale"),
                "observed": decision.get("observed"),
            }
        )

    unresolved = sum(
        counts[name]
        for name in (
            "collect_more",
            "investigate_instability",
            "manual_tradeoff_review",
        )
    )
    portfolio_state = (
        "ready_for_manual_category_review"
        if unresolved == 0
        else "mixed_evidence_requires_followup"
    )

    return {
        "schema_version": 1,
        "portfolio_state": portfolio_state,
        "category_count": len(rows),
        "unresolved_category_count": unresolved,
        "decision_counts": dict(sorted(counts.items())),
        "categories": sorted(rows, key=lambda row: row["category"]),
        "policy": {
            "category_isolation": True,
            "global_target_collapse": False,
            "safe_to_auto_apply": False,
            "automatic_routing_mutation": False,
            "automatic_hive_config_mutation": False,
        },
    }


def cross_target_portfolio_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Cross-Target Portfolio",
        "",
        f"State: **{report['portfolio_state']}**",
        f"Categories: **{report['category_count']}**",
        f"Unresolved: **{report['unresolved_category_count']}**",
        "",
        "| Category | Decision | Operator state | Manual target preference |",
        "|---|---|---|---|",
    ]
    for row in report["categories"]:
        target = (
            f"`{row['manual_target_preference']}`"
            if row["manual_target_preference"]
            else "—"
        )
        lines.append(
            f"| {row['category']} | `{row['decision']}` | "
            f"`{row['operator_state']}` | {target} |"
        )
    lines.extend(
        [
            "",
            (
                "Categories remain isolated. This portfolio never collapses them "
                "into one global execution target and never mutates routing or Hive configuration."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _load_decision(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CrossTargetPortfolioError("decision artifact root must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate category-isolated cross-target decisions for manual review."
    )
    parser.add_argument("--decision", action="append", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    try:
        report = build_cross_target_portfolio(
            [_load_decision(path) for path in args.decision]
        )
    except (OSError, json.JSONDecodeError, CrossTargetPortfolioError) as exc:
        print(f"cross-target portfolio error: {exc}")
        return 2

    markdown = cross_target_portfolio_markdown(report)
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
