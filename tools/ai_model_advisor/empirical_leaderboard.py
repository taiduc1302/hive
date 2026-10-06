from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .feedback import EXACT_FEEDBACK_MIN, PAIRED_EFFICIENCY_MIN, FeedbackStore
from .feedback_report import build_feedback_audit

PROMOTION_SCORE_MARGIN = 1.0
QUALITY_PROMOTION_MARGIN = 1.0
MAX_QUALITY_REGRESSION_FOR_EFFICIENCY_PROMOTION = 0.25


def _controlled(row: dict[str, Any]) -> bool:
    return row["effort"] != "observed" and row["execution_mode"] != "hive_agent_loop"


def _outcome_score(row: dict[str, Any]) -> float:
    observations = int(row["observations"])
    if observations <= 0:
        return 0.0
    weighted = float(row["success"]) + 0.5 * float(row["partial"])
    return round(weighted / observations, 6)


def _configuration(row: dict[str, Any]) -> dict[str, Any]:
    quality_adjustment = float(row["quality_adjustment"])
    efficiency_adjustment = float(row["efficiency_adjustment"])
    return {
        "model_id": row["model_id"],
        "effort": row["effort"],
        "execution_mode": row["execution_mode"],
        "observations": int(row["observations"]),
        "success": int(row["success"]),
        "partial": int(row["partial"]),
        "failure": int(row["failure"]),
        "outcome_score": _outcome_score(row),
        "average_retries": float(row["average_retries"]),
        "median_latency_seconds": row["median_latency_seconds"],
        "median_cost_usd": row["median_cost_usd"],
        "paired_comparable_tasks": int(row["paired_comparable_tasks"]),
        "quality_ready": bool(row["exact_quality_eligible"]),
        "efficiency_ready": bool(row["paired_efficiency_eligible"]),
        "quality_adjustment": quality_adjustment,
        "efficiency_adjustment": efficiency_adjustment,
        "empirical_score": round(quality_adjustment + efficiency_adjustment, 3),
    }


def _decision(eligible: list[dict[str, Any]]) -> dict[str, Any]:
    if len(eligible) < 2:
        return {
            "status": "insufficient_evidence",
            "winner": eligible[0] if eligible else None,
            "runner_up": None,
            "score_margin": None,
            "quality_margin": None,
            "reason": ("At least two controlled configurations with exact quality evidence are required before changing routing."),
        }

    ranked = sorted(
        eligible,
        key=lambda row: (
            row["empirical_score"],
            row["quality_adjustment"],
            row["outcome_score"],
            row["observations"],
            row["model_id"],
            row["effort"],
            row["execution_mode"],
        ),
        reverse=True,
    )
    winner = ranked[0]
    runner_up = ranked[1]
    score_margin = round(winner["empirical_score"] - runner_up["empirical_score"], 3)
    quality_margin = round(
        winner["quality_adjustment"] - runner_up["quality_adjustment"],
        3,
    )

    if quality_margin >= QUALITY_PROMOTION_MARGIN:
        status = "promote"
        reason = "Winner has enough exact outcome evidence and clears the conservative quality margin over the runner-up."
    elif (
        score_margin >= PROMOTION_SCORE_MARGIN
        and winner["efficiency_ready"]
        and runner_up["efficiency_ready"]
        and quality_margin >= -MAX_QUALITY_REGRESSION_FOR_EFFICIENCY_PROMOTION
    ):
        status = "promote"
        reason = "Winner clears the total empirical margin with paired efficiency evidence on both sides and no material quality regression."
    elif score_margin >= PROMOTION_SCORE_MARGIN:
        status = "collect_more"
        reason = (
            "A meaningful score gap exists, but the evidence is not balanced enough to promote safely. Collect more paired or exact observations."
        )
    else:
        status = "hold"
        reason = "Evidence is sufficient to compare the leading configurations, but the observed gap is too small to justify changing routing."

    return {
        "status": status,
        "winner": winner,
        "runner_up": runner_up,
        "score_margin": score_margin,
        "quality_margin": quality_margin,
        "reason": reason,
    }


def build_empirical_leaderboard(store: FeedbackStore) -> dict[str, Any]:
    """Build a conservative category-level leaderboard from recorded outcomes.

    The leaderboard is intentionally downstream of the existing feedback audit.
    It does not change routing by itself. A configuration is eligible for a
    routing decision only when it is controlled and has the exact-quality sample
    threshold. Cost/latency can decide a close race only when both leading
    configurations have enough successful paired task IDs.
    """
    audit = build_feedback_audit(store)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_uncontrolled = 0

    for source in audit["rows"]:
        if not _controlled(source):
            excluded_uncontrolled += 1
            continue
        grouped[source["category"]].append(_configuration(source))

    categories: list[dict[str, Any]] = []
    for category, configurations in sorted(grouped.items()):
        ranked = sorted(
            configurations,
            key=lambda row: (
                row["empirical_score"],
                row["quality_adjustment"],
                row["outcome_score"],
                row["observations"],
                row["model_id"],
            ),
            reverse=True,
        )
        eligible = [row for row in ranked if row["quality_ready"]]
        categories.append(
            {
                "category": category,
                "configurations": ranked,
                "eligible_configurations": len(eligible),
                "decision": _decision(eligible),
            }
        )

    decisions = [category["decision"]["status"] for category in categories]
    return {
        "records": audit["records"],
        "thresholds": {
            "exact_quality_observations": EXACT_FEEDBACK_MIN,
            "paired_efficiency_tasks": PAIRED_EFFICIENCY_MIN,
            "promotion_score_margin": PROMOTION_SCORE_MARGIN,
            "quality_promotion_margin": QUALITY_PROMOTION_MARGIN,
            "max_quality_regression_for_efficiency_promotion": (MAX_QUALITY_REGRESSION_FOR_EFFICIENCY_PROMOTION),
        },
        "excluded_uncontrolled_configurations": excluded_uncontrolled,
        "categories": categories,
        "decision_counts": {status: decisions.count(status) for status in ("promote", "hold", "collect_more", "insufficient_evidence")},
    }


def empirical_leaderboard_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Empirical Leaderboard",
        "",
        f"Feedback records: **{report['records']}**",
        (f"Uncontrolled historical configurations excluded from decisions: **{report['excluded_uncontrolled_configurations']}**"),
        "",
        "This report is decision support only. It never rewrites routing rules automatically.",
        "",
    ]

    if not report["categories"]:
        lines.extend(
            [
                "No controlled category evidence is available yet.",
                "",
                "Record explicit model + effort + execution outcomes before comparing models.",
                "",
            ]
        )
        return "\n".join(lines)

    for category in report["categories"]:
        decision = category["decision"]
        lines.extend(
            [
                f"## {category['category']}",
                "",
                f"Decision: **{decision['status']}** — {decision['reason']}",
                "",
                "| Rank | Configuration | N | Outcome score | Retries | Paired | Quality Δ | Efficiency Δ | Empirical score |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for index, row in enumerate(category["configurations"], start=1):
            configuration = f"`{row['model_id']} / {row['effort']} / {row['execution_mode']}`"
            lines.append(
                f"| {index} | {configuration} | {row['observations']} | "
                f"{row['outcome_score']:.3f} | {row['average_retries']:.3f} | "
                f"{row['paired_comparable_tasks']} | {row['quality_adjustment']:+.3f} | "
                f"{row['efficiency_adjustment']:+.3f} | {row['empirical_score']:+.3f} |"
            )
        if decision["winner"] is not None and decision["runner_up"] is not None:
            lines.extend(
                [
                    "",
                    (f"Top-vs-runner score margin: **{decision['score_margin']:+.3f}**; quality margin: **{decision['quality_margin']:+.3f}**."),
                ]
            )
        lines.append("")

    lines.extend(
        [
            "## Guardrails",
            "",
            (
                f"- At least {EXACT_FEEDBACK_MIN} exact controlled observations are required "
                "per configuration before it can influence a leaderboard decision."
            ),
            (f"- Cost/latency can break a close race only after both leaders have at least {PAIRED_EFFICIENCY_MIN} comparable successful task IDs."),
            "- Historical `observed / hive_agent_loop` telemetry is shown elsewhere but cannot win this leaderboard.",
            "- A `promote` result is a recommendation for router review, not an automatic policy mutation.",
            "",
        ]
    )
    return "\n".join(lines)


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a conservative empirical model leaderboard from Advisor feedback.")
    parser.add_argument("--feedback", required=True, help="Feedback JSONL path")
    parser.add_argument("--output", help="Markdown output path")
    parser.add_argument("--json-output", help="JSON output path")
    args = parser.parse_args(argv)

    report = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    markdown = empirical_leaderboard_markdown(report)
    _write(args.output, markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, indent=2, sort_keys=True) + "\n")
    if not args.output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
