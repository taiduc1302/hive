from __future__ import annotations

from collections import Counter
from typing import Any

from .feedback import (
    CROSS_CONFIG_FEEDBACK_MIN,
    EXACT_FEEDBACK_MIN,
    PAIRED_EFFICIENCY_MIN,
    FeedbackStore,
)
from .feedback_report import build_feedback_audit


def _is_controlled_config(effort: str, execution_mode: str) -> bool:
    return effort != "observed" and execution_mode != "hive_agent_loop"


def _next_action(row: dict[str, Any]) -> str:
    if not row["controlled_config"]:
        return (
            "Run controlled attempts with an explicit effort and execution mode; "
            "historical Hive observations are model-level evidence only."
        )
    if row["exact_quality_remaining"] > 0:
        return (
            f"Collect {row['exact_quality_remaining']} more outcome run(s) for this exact "
            "model + effort + execution configuration."
        )
    if row["paired_efficiency_remaining"] > 0:
        return (
            f"Collect {row['paired_efficiency_remaining']} more successful paired task ID(s) "
            "against at least one different configuration."
        )
    return "Evidence-ready; keep the configuration as a benchmark anchor and refresh periodically."


def build_experiment_readiness(store: FeedbackStore) -> dict[str, Any]:
    """Translate empirical-routing thresholds into an actionable collection plan.

    This report does not change routing. It explains which observed
    configurations already have enough evidence and what evidence is still
    missing. Historical Hive imports with ``effort=observed`` or
    ``execution=hive_agent_loop`` are intentionally not treated as controlled
    effort/mode experiments.
    """
    audit = build_feedback_audit(store)
    rows: list[dict[str, Any]] = []

    for source in audit["rows"]:
        controlled = _is_controlled_config(source["effort"], source["execution_mode"])
        exact_remaining = max(0, EXACT_FEEDBACK_MIN - int(source["observations"]))
        cross_remaining = max(
            0,
            CROSS_CONFIG_FEEDBACK_MIN - int(source["model_category_observations"]),
        )
        paired_remaining = max(
            0,
            PAIRED_EFFICIENCY_MIN - int(source["paired_comparable_tasks"]),
        )
        if source["exact_quality_eligible"]:
            quality_evidence = "exact"
        elif source["cross_config_eligible"]:
            quality_evidence = "same-model fallback"
        else:
            quality_evidence = "below threshold"

        row = {
            "category": source["category"],
            "model_id": source["model_id"],
            "effort": source["effort"],
            "execution_mode": source["execution_mode"],
            "controlled_config": controlled,
            "observations": source["observations"],
            "quality_evidence": quality_evidence,
            "exact_quality_remaining": exact_remaining,
            "cross_config_remaining": cross_remaining,
            "paired_comparable_tasks": source["paired_comparable_tasks"],
            "paired_efficiency_remaining": paired_remaining,
            "paired_efficiency_ready": bool(source["paired_efficiency_eligible"]),
            "quality_adjustment": source["quality_adjustment"],
            "efficiency_adjustment": source["efficiency_adjustment"],
        }
        row["evidence_ready"] = bool(
            controlled
            and source["exact_quality_eligible"]
            and source["paired_efficiency_eligible"]
        )
        row["next_action"] = _next_action(row)
        rows.append(row)

    category_counts: dict[str, Counter[str]] = {}
    for row in rows:
        counter = category_counts.setdefault(row["category"], Counter())
        counter["configs"] += 1
        if row["controlled_config"]:
            counter["controlled"] += 1
        if row["evidence_ready"]:
            counter["ready"] += 1
        if row["quality_evidence"] != "below threshold":
            counter["quality_active"] += 1
        if row["paired_efficiency_ready"]:
            counter["efficiency_ready"] += 1

    categories = [
        {
            "category": category,
            "configs": counts["configs"],
            "controlled_configs": counts["controlled"],
            "evidence_ready_configs": counts["ready"],
            "quality_active_configs": counts["quality_active"],
            "efficiency_ready_configs": counts["efficiency_ready"],
        }
        for category, counts in sorted(category_counts.items())
    ]

    return {
        "records": audit["records"],
        "thresholds": audit["thresholds"],
        "categories": categories,
        "rows": rows,
        "evidence_ready_configs": sum(1 for row in rows if row["evidence_ready"]),
        "controlled_configs": sum(1 for row in rows if row["controlled_config"]),
    }


def experiment_readiness_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Readiness",
        "",
        f"Feedback records: **{report['records']}**",
        f"Controlled configurations observed: **{report['controlled_configs']}**",
        f"Fully evidence-ready configurations: **{report['evidence_ready_configs']}**",
        "",
        "A configuration is fully evidence-ready only when it has explicit effort/execution metadata, "
        "enough exact outcome observations, and enough successful paired task IDs for efficiency comparison.",
        "",
    ]

    if not report["rows"]:
        lines.extend(
            [
                "No empirical history is available yet.",
                "",
                "Start by recording controlled model attempts with explicit effort, execution mode, task category, and stable task IDs.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## Category coverage",
            "",
            "| Category | Configs | Controlled | Quality active | Paired efficiency ready | Fully ready |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for category in report["categories"]:
        lines.append(
            f"| {category['category']} | {category['configs']} | {category['controlled_configs']} | "
            f"{category['quality_active_configs']} | {category['efficiency_ready_configs']} | "
            f"{category['evidence_ready_configs']} |"
        )

    lines.extend(
        [
            "",
            "## Next evidence to collect",
            "",
            "| Category | Configuration | Quality evidence | Exact runs needed | Paired tasks needed | Next action |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for row in report["rows"]:
        configuration = f"`{row['model_id']} / {row['effort']} / {row['execution_mode']}`"
        lines.append(
            f"| {row['category']} | {configuration} | {row['quality_evidence']} | "
            f"{row['exact_quality_remaining']} | {row['paired_efficiency_remaining']} | "
            f"{row['next_action']} |"
        )

    lines.extend(
        [
            "",
            (
                "Historical `observed / hive_agent_loop` rows can strengthen model-level confidence "
                "after the conservative fallback threshold, but they cannot prove which reasoning "
                "effort or orchestration mode caused the result."
            ),
            "",
        ]
    )
    return "\n".join(lines)
