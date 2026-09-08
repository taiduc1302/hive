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


def _median(values: list[float]) -> float | None:
    return round(float(median(values)), 6) if values else None


def _side_stats(records: list[UsageRecord]) -> dict[str, Any]:
    outcome_scores = [_OUTCOME_VALUE[record.outcome] for record in records]
    retries = [record.retries for record in records]
    costs = [float(record.cost_usd) for record in records if record.cost_usd is not None]
    latencies = [
        float(record.latency_seconds)
        for record in records
        if record.latency_seconds is not None
    ]
    return {
        "paired_observations": len(records),
        "success": sum(record.outcome == "success" for record in records),
        "partial": sum(record.outcome == "partial" for record in records),
        "failure": sum(record.outcome == "failure" for record in records),
        "mean_outcome_score": (
            round(mean(outcome_scores), 4) if outcome_scores else None
        ),
        "mean_retries": round(mean(retries), 4) if retries else None,
        "median_cost_usd": _median(costs),
        "median_latency_seconds": _median(latencies),
    }


def _leader(a: float | None, b: float | None, *, lower_is_better: bool) -> str:
    if a is None or b is None or a == b:
        return "tie"
    if lower_is_better:
        return "A" if a < b else "B"
    return "A" if a > b else "B"


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
        if (
            primary_records[0].outcome != "success"
            or challenger_records[0].outcome != "success"
        ):
            continue
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
    if decision in {"tie", "tradeoff"}:
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

    primary_records = [primary_by_task[task_id][0] for task_id in shared_task_ids]
    challenger_records = [
        challenger_by_task[task_id][0] for task_id in shared_task_ids
    ]
    primary_stats = _side_stats(primary_records)
    challenger_stats = _side_stats(challenger_records)

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

    quality_leader = _leader(
        primary_stats["mean_outcome_score"],
        challenger_stats["mean_outcome_score"],
        lower_is_better=False,
    )
    retry_leader = _leader(
        primary_stats["mean_retries"],
        challenger_stats["mean_retries"],
        lower_is_better=True,
    )
    cost_leader = _leader(
        primary_stats["median_cost_usd"],
        challenger_stats["median_cost_usd"],
        lower_is_better=True,
    )
    latency_leader = _leader(
        primary_stats["median_latency_seconds"],
        challenger_stats["median_latency_seconds"],
        lower_is_better=True,
    )

    efficiency_tasks, efficiency_delta = _paired_efficiency(
        primary_by_task,
        challenger_by_task,
        shared_task_ids,
        workload,
    )
    if (
        efficiency_tasks >= PAIRED_EFFICIENCY_MIN
        and efficiency_delta > _EFFICIENCY_TIE_EPSILON
    ):
        weighted_efficiency_leader = "A"
    elif (
        efficiency_tasks >= PAIRED_EFFICIENCY_MIN
        and efficiency_delta < -_EFFICIENCY_TIE_EPSILON
    ):
        weighted_efficiency_leader = "B"
    else:
        weighted_efficiency_leader = "tie"

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
        else:
            secondary = [
                leader
                for leader in (retry_leader, weighted_efficiency_leader)
                if leader != "tie"
            ]
            if secondary and len(set(secondary)) == 1:
                winner_side = "primary" if secondary[0] == "A" else "challenger"
                decision = f"{winner_side}_leads"
                decision_basis = "paired_efficiency"
            elif secondary:
                decision = "tradeoff"
                decision_basis = "tradeoff"
            else:
                decision = "tie"
                decision_basis = "tie"

    if decision_basis == "outcome_quality":
        conclusion = "quality_lead"
    elif decision_basis == "paired_efficiency":
        conclusion = "efficiency_lead"
    elif decision_basis == "tradeoff":
        conclusion = "tradeoff"
    elif decision_basis == "tie":
        conclusion = "tie"
    else:
        conclusion = "insufficient_evidence"
    suggested_winner = (
        "A" if winner_side == "primary" else "B" if winner_side == "challenger" else None
    )

    confidence, confidence_score = _confidence(
        paired_tasks,
        primary_wins,
        challenger_wins,
        quality_delta,
        efficiency_delta,
        decision,
    )
    policy_ready = paired_tasks >= required_pairs

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
        "required_paired_tasks": required_pairs,
        "additional_paired_tasks_needed": max(0, required_pairs - paired_tasks),
        "ambiguous_task_ids": ambiguous_task_ids,
        "incomplete_task_ids": incomplete_task_ids,
        "primary_records": len(primary_records),
        "challenger_records": len(challenger_records),
        "primary_quality_score": primary_quality_score,
        "challenger_quality_score": challenger_quality_score,
        "quality_delta_primary_minus_challenger": quality_delta,
        "primary_quality_wins": primary_wins,
        "challenger_quality_wins": challenger_wins,
        "quality_ties": quality_ties,
        "efficiency_paired_tasks": efficiency_tasks,
        "efficiency_delta_primary_advantage": efficiency_delta,
        "weighted_efficiency_leader": weighted_efficiency_leader,
        "decision": decision,
        "decision_basis": decision_basis,
        "winner_side": winner_side,
        "winner": pair[winner_side] if winner_side else None,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "policy_ready": policy_ready,
        "A": primary_stats,
        "B": challenger_stats,
        "quality_leader": quality_leader,
        "retry_leader": retry_leader,
        "cost_leader": cost_leader,
        "latency_leader": latency_leader,
        "conclusion": conclusion,
        "suggested_winner": suggested_winner,
    }


def evaluate_experiment_plan(
    plan: dict[str, Any],
    feedback: FeedbackStore,
) -> dict[str, Any]:
    """Evaluate feedback recorded against a generated experiment plan.

    Only task IDs under each experiment's own template prefix are considered.
    Ambiguous duplicate attempts are excluded instead of averaged. Outcome
    quality is evaluated before secondary retry/cost/latency evidence.
    Confidence is an evidence-strength heuristic, not a statistical probability.
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
        "paired_task_threshold": PAIRED_EFFICIENCY_MIN,
        "experiments": len(results),
        "evidence_ready": len(ready),
        "policy_ready_experiments": len(ready),
        "decided_experiments": len(decided),
        "unresolved_experiments": len(results) - len(decided),
        "confidence_note": (
            "confidence_score is a heuristic evidence-strength indicator, "
            "not a statistical probability or p-value"
        ),
        "evaluations": results,
        "results": results,
    }


def experiment_evaluation_markdown(report: dict[str, Any]) -> str:
    results = report.get("results") or report.get("evaluations") or []
    lines = [
        "# AI Model Advisor Experiment Evaluation",
        "",
        f"Experiments evaluated: **{report['experiments']}**",
        f"Policy-ready: **{report['policy_ready_experiments']}**",
        f"Decided: **{report['decided_experiments']}**",
        f"Unresolved: **{report['unresolved_experiments']}**",
        f"Minimum paired tasks: **{report['paired_task_threshold']}**",
        "",
        f"Note: {report['confidence_note']}.",
        "",
        (
            "The evaluator is descriptive only and never writes routing policy. "
            "Ambiguous duplicate attempts are excluded rather than guessed."
        ),
        "",
    ]

    if not results:
        lines.extend(["No experiment pairs were present in the supplied plan.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "| Category | Kind | Paired | Quality A/B | Efficiency pairs | Decision | Confidence | Ready |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
    )
    for result in results:
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
    for result in results:
        lines.extend(
            [
                f"### {result['category']} / {result['kind']} / {result['experiment_id']}",
                "",
                f"- A: `{_config_text(result['primary'])}`",
                f"- B: `{_config_text(result['challenger'])}`",
                f"- Task ID prefix: `{result['task_id_prefix']}`",
                f"- Complete paired tasks: **{result['paired_tasks']}**",
                (
                    "- Ambiguous duplicate tasks excluded: "
                    f"**{len(result['ambiguous_task_ids'])}**"
                ),
                (
                    "- Incomplete one-sided tasks: "
                    f"**{len(result['incomplete_task_ids'])}**"
                ),
                (
                    "- Quality wins A/B/tie: "
                    f"**{result['primary_quality_wins']}/"
                    f"{result['challenger_quality_wins']}/{result['quality_ties']}**"
                ),
                f"- Quality leader: **{result['quality_leader']}**",
                f"- Retry leader: **{result['retry_leader']}**",
                f"- Cost leader: **{result['cost_leader']}**",
                f"- Latency leader: **{result['latency_leader']}**",
                (
                    "- Quality delta A-B: "
                    f"**{result['quality_delta_primary_minus_challenger']:+.3f}**"
                ),
                (
                    "- Workload-weighted efficiency delta (positive favors A): "
                    f"**{result['efficiency_delta_primary_advantage']:+.3f}** "
                    f"across {result['efficiency_paired_tasks']} paired tasks"
                ),
                f"- Conclusion: **{result['conclusion']}**",
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
