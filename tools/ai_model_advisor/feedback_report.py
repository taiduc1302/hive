from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

from .feedback import FeedbackStore, UsageRecord

_EXACT_MIN = 3
_CROSS_CONFIG_MIN = 6


def _category(record: UsageRecord) -> str:
    return record.task_category or "untagged"


def _median(values: list[float]) -> float | None:
    return round(float(median(values)), 6) if values else None


def build_feedback_audit(store: FeedbackStore) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[UsageRecord]] = defaultdict(list)
    model_category_counts: Counter[tuple[str, str]] = Counter()

    for record in store.records:
        category = _category(record)
        key = (category, record.model_id, record.effort, record.execution_mode)
        grouped[key].append(record)
        model_category_counts[(category, record.model_id)] += 1

    rows: list[dict[str, Any]] = []
    for (category, model_id, effort, execution_mode), records in sorted(grouped.items()):
        outcomes = Counter(record.outcome for record in records)
        latencies = [
            float(record.latency_seconds)
            for record in records
            if record.latency_seconds is not None
        ]
        costs = [float(record.cost_usd) for record in records if record.cost_usd is not None]
        task_ids = {record.task_id for record in records if record.task_id}
        source_ids = {record.source_id for record in records if record.source_id}
        task_category = None if category == "untagged" else category
        comparable_tasks, efficiency_adjustment = store.efficiency_summary(
            model_id,
            effort,
            execution_mode,
            task_category,
        )
        exact_count = len(records)
        category_model_count = model_category_counts[(category, model_id)]
        rows.append(
            {
                "category": category,
                "model_id": model_id,
                "effort": effort,
                "execution_mode": execution_mode,
                "observations": exact_count,
                "success": outcomes["success"],
                "partial": outcomes["partial"],
                "failure": outcomes["failure"],
                "average_retries": round(
                    sum(record.retries for record in records) / exact_count,
                    3,
                ),
                "latency_samples": len(latencies),
                "median_latency_seconds": _median(latencies),
                "cost_samples": len(costs),
                "median_cost_usd": _median(costs),
                "unique_task_ids": len(task_ids),
                "unique_source_ids": len(source_ids),
                "exact_quality_eligible": exact_count >= _EXACT_MIN,
                "model_category_observations": category_model_count,
                "cross_config_eligible": category_model_count >= _CROSS_CONFIG_MIN,
                "quality_adjustment": store.adjustment(
                    model_id,
                    effort,
                    execution_mode,
                    task_category,
                ),
                "paired_comparable_tasks": comparable_tasks,
                "paired_efficiency_eligible": comparable_tasks >= _EXACT_MIN,
                "efficiency_adjustment": efficiency_adjustment,
            }
        )

    return {
        "records": len(store.records),
        "models": len({record.model_id for record in store.records}),
        "categories": sorted({_category(record) for record in store.records}),
        "unique_task_ids": len({record.task_id for record in store.records if record.task_id}),
        "unique_source_ids": len({record.source_id for record in store.records if record.source_id}),
        "thresholds": {
            "exact_quality_observations": _EXACT_MIN,
            "cross_config_observations": _CROSS_CONFIG_MIN,
            "paired_efficiency_tasks": _EXACT_MIN,
        },
        "rows": rows,
    }


def feedback_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Feedback Audit",
        "",
        f"Records: **{audit['records']}**",
        f"Models: **{audit['models']}**",
        f"Categories: **{len(audit['categories'])}**",
        f"Unique task IDs: **{audit['unique_task_ids']}**",
        f"Unique importer source IDs: **{audit['unique_source_ids']}**",
        "",
        "## Evidence thresholds",
        "",
        f"- Exact configuration quality: **{audit['thresholds']['exact_quality_observations']} observations**",
        f"- Same-model cross-config fallback: **{audit['thresholds']['cross_config_observations']} category-compatible observations**",
        f"- Paired cost/latency efficiency: **{audit['thresholds']['paired_efficiency_tasks']} comparable task IDs**",
        "",
    ]

    rows = audit["rows"]
    if not rows:
        lines.extend(
            [
                "No feedback records are present. The static registry is therefore driving recommendations.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## Evidence by configuration",
            "",
            "| Category | Model | Effort | Execution | N | S/P/F | Exact | Cross-config | Paired tasks | Quality Δ | Efficiency Δ |",
            "|---|---|---|---|---:|---|---|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        outcome = f"{row['success']}/{row['partial']}/{row['failure']}"
        exact = "yes" if row["exact_quality_eligible"] else "no"
        cross = "yes" if row["cross_config_eligible"] else "no"
        lines.append(
            f"| {row['category']} | `{row['model_id']}` | {row['effort']} | "
            f"{row['execution_mode']} | {row['observations']} | {outcome} | {exact} | "
            f"{cross} | {row['paired_comparable_tasks']} | {row['quality_adjustment']:+.3f} | "
            f"{row['efficiency_adjustment']:+.3f} |"
        )

    lines.extend(["", "## Coverage details", ""])
    for row in rows:
        latency = (
            f"{row['median_latency_seconds']:.3f}s median across {row['latency_samples']} records"
            if row["median_latency_seconds"] is not None
            else "no latency observations"
        )
        cost = (
            f"${row['median_cost_usd']:.6f} median across {row['cost_samples']} records"
            if row["median_cost_usd"] is not None
            else "no cost observations"
        )
        lines.append(
            f"- **{row['category']} / {row['model_id']} / {row['effort']} / {row['execution_mode']}**: "
            f"avg retries {row['average_retries']:.3f}; {latency}; {cost}; "
            f"{row['unique_task_ids']} task IDs; {row['unique_source_ids']} source IDs."
        )

    lines.extend(
        [
            "",
            "A non-zero empirical adjustment should be explainable by this report. Rows below threshold remain historical evidence but do not independently change routing.",
            "",
        ]
    )
    return "\n".join(lines)
