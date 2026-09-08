from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from .feedback import PAIRED_EFFICIENCY_MIN, FeedbackStore, UsageRecord

_OUTCOME_SCORE = {"success": 1.0, "partial": 0.5, "failure": 0.0}


def _config_key_from_dict(config: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(config.get("model_id") or ""),
        str(config.get("effort") or ""),
        str(config.get("execution_mode") or ""),
    )


def _config_key_from_record(record: UsageRecord) -> tuple[str, str, str]:
    return record.model_id, record.effort, record.execution_mode


def _task_prefix(pair: dict[str, Any]) -> str:
    template = str(pair.get("task_id_template") or "")
    return template.split("{nn}", 1)[0] if "{nn}" in template else template


def _side_records(
    store: FeedbackStore,
    category: str,
    config: dict[str, Any],
    task_prefix: str,
) -> dict[str, list[UsageRecord]]:
    key = _config_key_from_dict(config)
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in store.records:
        if not record.task_id or not record.task_id.startswith(task_prefix):
            continue
        if record.task_category != category:
            continue
        if _config_key_from_record(record) != key:
            continue
        grouped[record.task_id].append(record)
    return grouped


def _median(values: list[float]) -> float | None:
    return round(float(median(values)), 6) if values else None


def _leader(a: float | None, b: float | None, *, lower_is_better: bool) -> str:
    if a is None or b is None or a == b:
        return "tie"
    if lower_is_better:
        return "A" if a < b else "B"
    return "A" if a > b else "B"


def _evaluate_pair(pair: dict[str, Any], store: FeedbackStore) -> dict[str, Any]:
    category = str(pair.get("category") or "")
    prefix = _task_prefix(pair)
    a_by_task = _side_records(store, category, pair["primary"], prefix)
    b_by_task = _side_records(store, category, pair["challenger"], prefix)
    all_tasks = sorted(set(a_by_task) | set(b_by_task))

    paired: list[tuple[str, UsageRecord, UsageRecord]] = []
    ambiguous: list[str] = []
    incomplete: list[str] = []
    for task_id in all_tasks:
        a_rows = a_by_task.get(task_id, [])
        b_rows = b_by_task.get(task_id, [])
        if len(a_rows) > 1 or len(b_rows) > 1:
            ambiguous.append(task_id)
            continue
        if len(a_rows) != 1 or len(b_rows) != 1:
            incomplete.append(task_id)
            continue
        paired.append((task_id, a_rows[0], b_rows[0]))

    def side_stats(index: int) -> dict[str, Any]:
        records = [item[index] for item in paired]
        outcome_scores = [_OUTCOME_SCORE[record.outcome] for record in records]
        costs = [float(record.cost_usd) for record in records if record.cost_usd is not None]
        latencies = [
            float(record.latency_seconds)
            for record in records
            if record.latency_seconds is not None
        ]
        retries = [record.retries for record in records]
        return {
            "paired_observations": len(records),
            "success": sum(record.outcome == "success" for record in records),
            "partial": sum(record.outcome == "partial" for record in records),
            "failure": sum(record.outcome == "failure" for record in records),
            "mean_outcome_score": (
                round(sum(outcome_scores) / len(outcome_scores), 4) if outcome_scores else None
            ),
            "mean_retries": round(sum(retries) / len(retries), 4) if retries else None,
            "median_cost_usd": _median(costs),
            "median_latency_seconds": _median(latencies),
        }

    a_stats = side_stats(1)
    b_stats = side_stats(2)
    quality_leader = _leader(
        a_stats["mean_outcome_score"],
        b_stats["mean_outcome_score"],
        lower_is_better=False,
    )
    retry_leader = _leader(
        a_stats["mean_retries"],
        b_stats["mean_retries"],
        lower_is_better=True,
    )
    cost_leader = _leader(
        a_stats["median_cost_usd"],
        b_stats["median_cost_usd"],
        lower_is_better=True,
    )
    latency_leader = _leader(
        a_stats["median_latency_seconds"],
        b_stats["median_latency_seconds"],
        lower_is_better=True,
    )

    paired_count = len(paired)
    if paired_count < PAIRED_EFFICIENCY_MIN:
        conclusion = "insufficient_evidence"
        suggested_winner = None
    elif quality_leader != "tie":
        conclusion = "quality_lead"
        suggested_winner = quality_leader
    else:
        secondary = [leader for leader in (retry_leader, cost_leader, latency_leader) if leader != "tie"]
        if secondary and len(set(secondary)) == 1:
            conclusion = "efficiency_lead"
            suggested_winner = secondary[0]
        elif secondary:
            conclusion = "tradeoff"
            suggested_winner = None
        else:
            conclusion = "tie"
            suggested_winner = None

    return {
        "experiment_id": pair.get("experiment_id"),
        "kind": pair.get("kind"),
        "category": category,
        "primary": pair["primary"],
        "challenger": pair["challenger"],
        "task_id_prefix": prefix,
        "paired_task_ids": [task_id for task_id, _, _ in paired],
        "paired_tasks": paired_count,
        "required_paired_tasks": PAIRED_EFFICIENCY_MIN,
        "ambiguous_task_ids": ambiguous,
        "incomplete_task_ids": incomplete,
        "A": a_stats,
        "B": b_stats,
        "quality_leader": quality_leader,
        "retry_leader": retry_leader,
        "cost_leader": cost_leader,
        "latency_leader": latency_leader,
        "conclusion": conclusion,
        "suggested_winner": suggested_winner,
    }


def evaluate_experiment_plan(plan: dict[str, Any], store: FeedbackStore) -> dict[str, Any]:
    evaluations = [
        _evaluate_pair(pair, store)
        for category in plan.get("categories", [])
        for pair in category.get("pairs", [])
    ]
    return {
        "paired_task_threshold": PAIRED_EFFICIENCY_MIN,
        "experiments": len(evaluations),
        "evidence_ready": sum(
            evaluation["paired_tasks"] >= PAIRED_EFFICIENCY_MIN
            for evaluation in evaluations
        ),
        "evaluations": evaluations,
    }


def _config_text(config: dict[str, Any]) -> str:
    return (
        f"{config.get('model_id')} / {config.get('effort')} / "
        f"{config.get('execution_mode')}"
    )


def experiment_evaluation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Evaluation",
        "",
        f"Experiments evaluated: **{report['experiments']}**",
        f"Evidence-ready experiments: **{report['evidence_ready']}**",
        f"Minimum paired tasks: **{report['paired_task_threshold']}**",
        "",
        (
            "This report is descriptive only. It does not write routing policy. "
            "Ambiguous duplicate attempts are excluded rather than guessed."
        ),
        "",
    ]
    if not report["evaluations"]:
        lines.extend(["No experiment pairs were present in the plan.", ""])
        return "\n".join(lines)

    for evaluation in report["evaluations"]:
        lines.extend(
            [
                f"## {evaluation['category']} / {evaluation['kind']}",
                "",
                f"Experiment: `{evaluation['experiment_id']}`",
                f"- A: `{_config_text(evaluation['primary'])}`",
                f"- B: `{_config_text(evaluation['challenger'])}`",
                f"- Complete paired tasks: **{evaluation['paired_tasks']}**",
                f"- Ambiguous duplicates excluded: **{len(evaluation['ambiguous_task_ids'])}**",
                f"- Incomplete one-sided tasks: **{len(evaluation['incomplete_task_ids'])}**",
                f"- Quality leader: **{evaluation['quality_leader']}**",
                f"- Retry leader: **{evaluation['retry_leader']}**",
                f"- Cost leader: **{evaluation['cost_leader']}**",
                f"- Latency leader: **{evaluation['latency_leader']}**",
                f"- Conclusion: **{evaluation['conclusion']}**",
            ]
        )
        if evaluation["suggested_winner"]:
            lines.append(f"- Suggested winner: **{evaluation['suggested_winner']}**")
        lines.append("")

    lines.extend(
        [
            "A quality lead takes precedence over efficiency. When quality is tied, retries, cost, "
            "and latency may provide a secondary lead; conflicting secondary metrics are reported "
            "as a trade-off rather than forcing a winner.",
            "",
        ]
    )
    return "\n".join(lines)
