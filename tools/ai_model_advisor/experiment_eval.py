from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any

from .feedback import EXACT_FEEDBACK_MIN, PAIRED_EFFICIENCY_MIN, FeedbackStore, UsageRecord

_OUTCOME_VALUE = {"success": 1.0, "partial": 0.5, "failure": 0.0}
_QUALITY_TIE_EPSILON = 0.05
_EFFICIENCY_TIE_EPSILON = 0.05


def _config_text(config: dict[str, Any]) -> str:
    return (
        f"{config['model_id']} / {config['effort']} / "
        f"{config['execution_mode']}"
    )


def _task_prefix(pair: dict[str, Any]) -> str:
    template = str(pair.get("task_id_template") or "")
    if "{nn}" in template:
        return template.split("{nn}", 1)[0]
    if template:
        return template
    return f"{pair['category']}-{pair['experiment_id']}-task-"


def _records_by_task(
    feedback: FeedbackStore,
    category: str,
    config: dict[str, Any],
    task_prefix: str,
) -> dict[str, list[UsageRecord]]:
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in feedback.records:
        if not record.task_id or not record.task_id.startswith(task_prefix):
            continue
        if record.task_category != category:
            continue
        if record.model_id != config["model_id"]:
            continue
        if record.effort != config["effort"]:
            continue
        if record.execution_mode != config["execution_mode"]:
            continue
        grouped[record.task_id].append(record)
    return dict(grouped)


def _task_quality(records: list[UsageRecord]) -> float:
    observed = mean(_OUTCOME_VALUE[record.outcome] for record in records)
    retry_penalty = min(0.20, mean(record.retries for record in records) * 0.05)
    return max(0.0, observed - retry_penalty)


def _success_metric(records: list[UsageRecord], field: str) -> float | None:
    values = [
        float(value)
        for record in records
        if record.outcome == "success"
        if (value := getattr(record, field)) is not None
    ]
    return float(median(values)) if values else None


def _metric_advantage(primary: float | None, challenger: float | None) -> float | None:
    if primary is None or challenger is None:
        return None
    scale = max(primary, challenger)
    if scale <= 0:
        return None
    return max(-0.5, min(0.5, (challenger - primary) / scale))


def _paired_efficiency(
    primary_by_task: dict[str, list[UsageRecord]],
    challenger_by_task: dict[str, list[UsageRecord]],
    shared_task_ids: list[str],
    workload: dict[str, Any],
) -> tuple[int, float]:
    latency_weight = max(
        0.0,
        min(1.0, float(workload.get("latency_sensitivity", 3.0)) / 5.0),
    )
    cost_weight = max(
        0.0,
        min(1.0, float(workload.get("cost_sensitivity", 3.0)) / 5.0),
    )
    task_scores: list[float] = []

    for task_id in shared_task_ids:
        primary_records = primary_by_task[task_id]
        challenger_records = challenger_by_task[task_id]
        metrics: list[tuple[float, float]] = []

        latency_advantage = _metric_advantage(
            _success_metric(primary_records, "latency_seconds"),
            _success_metric(challenger_records, "latency_seconds"),
        )
        if latency_advantage is not None and latency_weight > 0:
            metrics.append((latency_advantage, latency_weight))

        cost_advantage = _metric_advantage(
            _success_metric(primary_records, "cost_usd"),
            _success_metric(challenger_records, "cost_usd"),
        )
        if cost_advantage is not None and cost_weight > 0:
            metrics.append((cost_advantage, cost_weight))

        if not metrics:
            continue
        weighted = sum(score * weight for score, weight in metrics)
        total_weight = sum(weight for _, weight in metrics)
        task_scores.append(weighted / total_weight)

    if not task_scores:
        return 0, 0.0
    return len(task_scores), round(mean(task_scores), 3)


def _confidence(
    paired_tasks: int,
    primary_wins: int,
    challenger_wins: int,
    quality_delta: float,
    efficiency_delta: float,
    decision: str,
) -> tuple[str, float]:
    if paired_tasks < EXACT_FEEDBACK_MIN:
        score = min(0.49, (paired_tasks / EXACT_FEEDBACK_MIN) * 0.45)
        return "insufficient", round(score, 2)

    sample_strength = min(1.0, (paired_tasks - 2) / 6.0)
    dominance = abs(primary_wins - challenger_wins) / max(1, paired_tasks)
    margin = min(1.0, abs(quality_delta) * 2.0 + abs(efficiency_delta))
    score = min(
        0.95,
        0.45 + 0.25 * sample_strength + 0.20 * dominance + 0.10 * margin,
    )
    if decision == "tie":
        score = min(score, 0.64)

    if score < 0.65:
        band = "low"
    elif score < 0.80:
        band = "medium"
    else:
        band = "high"
    return band, round(score, 2)


def _evaluate_pair(
    pair: dict[str, Any],
    workload: dict[str, Any],
    feedback: FeedbackStore,
) -> dict[str, Any]:
    category = str(pair["category"])
    prefix = _task_prefix(pair)
    primary = pair["primary"]
    challenger = pair["challenger"]
    primary_by_task = _records_by_task(feedback, category, primary, prefix)
    challenger_by_task = _records_by_task(feedback, category, challenger, prefix)

    all_task_ids = sorted(set(primary_by_task) | set(challenger_by_task))
    shared_task_ids: list[str] = []
    ambiguous_task_ids: list[str] = []
    incomplete_task_ids: list[str] = []
    for task_id in all_task_ids:
        primary_records = primary_by_task.get(task_id, [])
        challenger_records = challenger_by_task.get(task_id, [])
        if len(primary_records) > 1 or len(challenger_records) > 1:
            ambiguous_task_ids.append(task_id)
            continue
        if len(primary_records) != 1 or len(challenger_records) != 1:
            incomplete_task_ids.append(task_id)
            continue
        shared_task_ids.append(task_id)

    primary_quality = [
        _task_quality(primary_by_task[task_id]) for task_id in shared_task_ids
    ]
    challenger_quality = [
        _task_quality(challenger_by_task[task_id]) for task_id in shared_task_ids
    ]
    primary_quality_score = round(mean(primary_quality), 3) if primary_quality else 0.0
    challenger_quality_score = (
        round(mean(challenger_quality), 3) if challenger_quality else 0.0
    )
    quality_delta = round(primary_quality_score - challenger_quality_score, 3)

    primary_wins = 0
    challenger_wins = 0
    quality_ties = 0
    for left, right in zip(primary_quality, challenger_quality, strict=True):
        delta = left - right
        if delta > _QUALITY_TIE_EPSILON:
            primary_wins += 1
        elif delta < -_QUALITY_TIE_EPSILON:
            challenger_wins += 1
        else:
            quality_ties += 1

    efficiency_tasks, efficiency_delta = _paired_efficiency(
        primary_by_task,
        challenger_by_task,
        shared_task_ids,
        workload,
    )

    paired_tasks = len(shared_task_ids)
    required_pairs = max(
        EXACT_FEEDBACK_MIN,
        int(pair.get("paired_tasks_required", PAIRED_EFFICIENCY_MIN)),
    )
    decision = "insufficient_evidence"
    winner_side: str | None = None
    decision_basis = "insufficient_evidence"

    if paired_tasks >= required_pairs:
        if quality_delta > _QUALITY_TIE_EPSILON:
            decision = "primary_leads"
            winner_side = "primary"
            decision_basis = "outcome_quality"
        elif quality_delta < -_QUALITY_TIE_EPSILON:
            decision = "challenger_leads"
            winner_side = "challenger"
            decision_basis = "outcome_quality"
        elif efficiency_tasks >= PAIRED_EFFICIENCY_MIN:
            if efficiency_delta > _EFFICIENCY_TIE_EPSILON:
                decision = "primary_leads"
                winner_side = "primary"
                decision_basis = "paired_efficiency"
            elif efficiency_delta < -_EFFICIENCY_TIE_EPSILON:
                decision = "challenger_leads"
                winner_side = "challenger"
                decision_basis = "paired_efficiency"
            else:
                decision = "tie"
                decision_basis = "tie"
        else:
            decision = "tie"
            decision_basis = "tie"

    confidence, confidence_score = _confidence(
        paired_tasks,
        primary_wins,
        challenger_wins,
        quality_delta,
        efficiency_delta,
        decision,
    )
    policy_ready = paired_tasks >= required_pairs and (
        decision_basis != "paired_efficiency"
        or efficiency_tasks >= PAIRED_EFFICIENCY_MIN
    )

    return {
        "experiment_id": pair["experiment_id"],
        "kind": pair["kind"],
        "priority": pair.get("priority"),
        "category": category,
        "task_id_prefix": prefix,
        "primary": primary,
        "challenger": challenger,
        "paired_task_ids": shared_task_ids,
        "paired_tasks": paired_tasks,
        "paired_tasks_required": required_pairs,
        "additional_paired_tasks_needed": max(0, required_pairs - paired_tasks),
        "ambiguous_task_ids": ambiguous_task_ids,
        "incomplete_task_ids": incomplete_task_ids,
        "primary_records": sum(len(records) for records in primary_by_task.values()),
        "challenger_records": sum(len(records) for records in challenger_by_task.values()),
        "primary_quality_score": primary_quality_score,
        "challenger_quality_score": challenger_quality_score,
        "quality_delta_primary_minus_challenger": quality_delta,
        "primary_quality_wins": primary_wins,
        "challenger_quality_wins": challenger_wins,
        "quality_ties": quality_ties,
        "efficiency_paired_tasks": efficiency_tasks,
        "efficiency_delta_primary_advantage": efficiency_delta,
        "decision": decision,
        "decision_basis": decision_basis,
        "winner_side": winner_side,
        "winner": pair[winner_side] if winner_side else None,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "policy_ready": policy_ready,
    }


def evaluate_experiment_plan(
    plan: dict[str, Any],
    feedback: FeedbackStore,
) -> dict[str, Any]:
    """Evaluate feedback recorded against a generated experiment plan.

    Only task IDs under each experiment's own template prefix are considered.
    Outcome quality is evaluated before cost/latency. Efficiency can decide a
    tie only after enough successful paired tasks exist. Confidence is an
    evidence-strength heuristic, not a statistical probability.
    """
    results: list[dict[str, Any]] = []
    for category in plan.get("categories", []):
        workload = category.get("workload") or {}
        for pair in category.get("pairs", []):
            results.append(_evaluate_pair(pair, workload, feedback))

    ready = [result for result in results if result["policy_ready"]]
    decided = [
        result
        for result in ready
        if result["decision"] in {"primary_leads", "challenger_leads"}
    ]
    return {
        "experiments": len(results),
        "policy_ready_experiments": len(ready),
        "decided_experiments": len(decided),
        "unresolved_experiments": len(results) - len(decided),
        "confidence_note": (
            "confidence_score is a heuristic evidence-strength indicator, "
            "not a statistical probability or p-value"
        ),
        "results": results,
    }


def experiment_evaluation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Evaluation",
        "",
        f"Experiments evaluated: **{report['experiments']}**",
        f"Policy-ready: **{report['policy_ready_experiments']}**",
        f"Decided: **{report['decided_experiments']}**",
        f"Unresolved: **{report['unresolved_experiments']}**",
        "",
        f"Note: {report['confidence_note']}.",
        "",
    ]

    if not report["results"]:
        lines.extend(["No experiment pairs were present in the supplied plan.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "| Category | Kind | Paired | Quality A/B | Efficiency pairs | Decision | Confidence | Ready |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
    )
    for result in report["results"]:
        quality = (
            f"{result['primary_quality_score']:.3f}/"
            f"{result['challenger_quality_score']:.3f}"
        )
        ready = "yes" if result["policy_ready"] else "no"
        lines.append(
            f"| {result['category']} | {result['kind']} | {result['paired_tasks']} | "
            f"{quality} | {result['efficiency_paired_tasks']} | {result['decision']} | "
            f"{result['confidence']} ({result['confidence_score']:.2f}) | {ready} |"
        )

    lines.extend(["", "## Experiment details", ""])
    for result in report["results"]:
        lines.extend(
            [
                f"### {result['category']} / {result['kind']} / {result['experiment_id']}",
                "",
                f"- A: `{_config_text(result['primary'])}`",
                f"- B: `{_config_text(result['challenger'])}`",
                f"- Task ID prefix: `{result['task_id_prefix']}`",
                f"- Paired tasks: **{result['paired_tasks']}**",
                f"- Ambiguous duplicate tasks excluded: **{len(result['ambiguous_task_ids'])}**",
                f"- Incomplete one-sided tasks: **{len(result['incomplete_task_ids'])}**",
                (
                    "- Quality wins A/B/tie: "
                    f"**{result['primary_quality_wins']}/"
                    f"{result['challenger_quality_wins']}/{result['quality_ties']}**"
                ),
                (
                    "- Quality delta A-B: "
                    f"**{result['quality_delta_primary_minus_challenger']:+.3f}**"
                ),
                (
                    "- Efficiency delta (positive favors A): "
                    f"**{result['efficiency_delta_primary_advantage']:+.3f}** "
                    f"across {result['efficiency_paired_tasks']} paired tasks"
                ),
                f"- Decision: **{result['decision']}** via `{result['decision_basis']}`",
                f"- Confidence: **{result['confidence']} ({result['confidence_score']:.2f})**",
                f"- Policy-ready: **{'yes' if result['policy_ready'] else 'no'}**",
                (
                    "- Additional paired tasks needed: "
                    f"**{result['additional_paired_tasks_needed']}**"
                ),
                "",
            ]
        )
    return "\n".join(lines)
