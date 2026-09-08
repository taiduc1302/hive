from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from .feedback import (
    EXACT_FEEDBACK_MIN,
    PAIRED_EFFICIENCY_MIN,
    FeedbackStore,
    UsageRecord,
)
from .models import ModelProfile, Recommendation, WorkloadProfile
from .recommend import RecommendationEngine


def _config_key(rec: Recommendation) -> tuple[str, str, str]:
    return rec.model_id, rec.effort, rec.execution_mode


def _model_profile(engine: RecommendationEngine, model_id: str) -> ModelProfile | None:
    matches = [model for model in engine.registry.models if model.model_id == model_id]
    return matches[0] if len(matches) == 1 else None


def _rank_same_model_configurations(
    engine: RecommendationEngine,
    model: ModelProfile,
    workload: WorkloadProfile,
    category: str,
) -> list[Recommendation]:
    preferred_effort = engine._effort(model, workload)
    preferred_mode = engine._execution_mode(model, workload)
    rows = [
        engine._score_configuration(
            model,
            workload,
            effort,
            mode,
            preferred_effort,
            preferred_mode,
            category,
        )
        for effort in model.efforts
        for mode in model.execution_modes
    ]
    rows.sort(
        key=lambda item: (
            item.score,
            item.configuration_adjustment,
            item.effort == preferred_effort,
            item.execution_mode == preferred_mode,
        ),
        reverse=True,
    )
    return rows


def _records_by_task(
    feedback: FeedbackStore,
    category: str,
    rec: Recommendation,
    task_prefix: str,
) -> dict[str, list[UsageRecord]]:
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in feedback.records:
        if not record.task_id or not record.task_id.startswith(task_prefix):
            continue
        if record.task_category != category:
            continue
        if record.model_id != rec.model_id:
            continue
        if record.effort != rec.effort:
            continue
        if record.execution_mode != rec.execution_mode:
            continue
        grouped[record.task_id].append(record)
    return dict(grouped)


def _experiment_id(
    category: str,
    kind: str,
    primary: Recommendation,
    challenger: Recommendation,
) -> str:
    raw = "|".join((category, kind, *_config_key(primary), *_config_key(challenger)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _pair(
    category: str,
    kind: str,
    primary: Recommendation,
    challenger: Recommendation,
    feedback: FeedbackStore,
    priority: int,
) -> dict[str, Any]:
    experiment_id = _experiment_id(category, kind, primary, challenger)
    task_prefix = f"{category}-{experiment_id}-task-"
    primary_by_task = _records_by_task(feedback, category, primary, task_prefix)
    challenger_by_task = _records_by_task(feedback, category, challenger, task_prefix)

    complete: list[str] = []
    successful: list[str] = []
    ambiguous: list[str] = []
    incomplete: list[str] = []
    all_task_ids = sorted(set(primary_by_task) | set(challenger_by_task))
    for task_id in all_task_ids:
        primary_records = primary_by_task.get(task_id, [])
        challenger_records = challenger_by_task.get(task_id, [])
        if len(primary_records) > 1 or len(challenger_records) > 1:
            ambiguous.append(task_id)
            continue
        if len(primary_records) != 1 or len(challenger_records) != 1:
            incomplete.append(task_id)
            continue
        complete.append(task_id)
        if (
            primary_records[0].outcome == "success"
            and challenger_records[0].outcome == "success"
        ):
            successful.append(task_id)

    quality_remaining = max(0, EXACT_FEEDBACK_MIN - len(complete))
    efficiency_remaining = max(0, PAIRED_EFFICIENCY_MIN - len(successful))
    has_any_observation = bool(all_task_ids)
    if not has_any_observation:
        status = "planned"
    elif quality_remaining > 0:
        status = "collecting"
    else:
        status = "ready"

    if status in {"planned", "collecting"}:
        next_action = {
            "type": "collect_paired_tasks",
            "paired_tasks_needed": quality_remaining,
        }
    else:
        next_action = {
            "type": "evaluate_saved_plan",
            "paired_tasks_needed": 0,
        }

    if kind == "model":
        rationale = (
            "Compare the current best configuration with the strongest different model "
            "on the same tasks."
        )
    elif kind == "effort":
        rationale = (
            "Hold model and execution mode constant so the comparison isolates reasoning effort."
        )
    elif kind == "execution":
        rationale = (
            "Hold model and reasoning effort constant so the comparison isolates execution mode."
        )
    else:
        raise ValueError(f"Unsupported experiment kind: {kind}")
    return {
        "experiment_id": experiment_id,
        "kind": kind,
        "priority": priority,
        "status": status,
        "next_action": next_action,
        "category": category,
        "primary": primary.as_dict(),
        "challenger": challenger.as_dict(),
        "complete_paired_task_ids": complete,
        "shared_successful_task_ids": successful,
        "ambiguous_task_ids": ambiguous,
        "incomplete_task_ids": incomplete,
        "complete_paired_tasks_observed": len(complete),
        "successful_paired_tasks_observed": len(successful),
        "quality_paired_tasks_required": EXACT_FEEDBACK_MIN,
        "quality_paired_tasks_remaining": quality_remaining,
        "quality_ready": quality_remaining == 0,
        # Backward-compatible efficiency aliases retained for existing consumers.
        "paired_tasks_observed": len(successful),
        "paired_tasks_required": PAIRED_EFFICIENCY_MIN,
        "paired_tasks_remaining": efficiency_remaining,
        "efficiency_ready": efficiency_remaining == 0,
        "task_id_template": f"{task_prefix}{{nn}}",
        "rationale": rationale,
    }


def build_experiment_plan(
    profiles: dict[str, WorkloadProfile],
    engine: RecommendationEngine,
    providers: list[str] | None = None,
    include_limited: bool = False,
) -> dict[str, Any]:
    """Build controlled A/B suggestions for each observed task category.

    The planner never executes models. It proposes stable same-task comparison
    pairs using the router's current ranking and counts already completed
    paired tasks from the feedback store. Same-model experiments isolate one
    variable at a time: reasoning effort or execution mode.
    """
    categories: list[dict[str, Any]] = []
    ordered = sorted(
        profiles.items(),
        key=lambda item: (-item[1].activity_count, item[0]),
    )

    for category, workload in ordered:
        recommendations = engine.recommend(
            workload,
            providers=providers,
            include_limited=include_limited,
            top_n=2,
        )
        if not recommendations:
            continue
        primary = recommendations[0]
        pairs: list[dict[str, Any]] = []

        if len(recommendations) > 1:
            pairs.append(
                _pair(
                    category,
                    "model",
                    primary,
                    recommendations[1],
                    engine.feedback,
                    priority=1,
                )
            )

        model = _model_profile(engine, primary.model_id)
        if model is not None:
            same_model = _rank_same_model_configurations(
                engine,
                model,
                workload,
                category,
            )
            effort_challenger = next(
                (
                    candidate
                    for candidate in same_model
                    if candidate.execution_mode == primary.execution_mode
                    and candidate.effort != primary.effort
                ),
                None,
            )
            if effort_challenger is not None:
                pairs.append(
                    _pair(
                        category,
                        "effort",
                        primary,
                        effort_challenger,
                        engine.feedback,
                        priority=2,
                    )
                )

            execution_challenger = next(
                (
                    candidate
                    for candidate in same_model
                    if candidate.effort == primary.effort
                    and candidate.execution_mode != primary.execution_mode
                ),
                None,
            )
            if execution_challenger is not None:
                pairs.append(
                    _pair(
                        category,
                        "execution",
                        primary,
                        execution_challenger,
                        engine.feedback,
                        priority=3,
                    )
                )

        categories.append(
            {
                "category": category,
                "activity_count": workload.activity_count,
                "workload": workload.as_dict(),
                "primary": primary.as_dict(),
                "pairs": pairs,
            }
        )

    all_pairs = [pair for item in categories for pair in item["pairs"]]
    return {
        "paired_quality_threshold": EXACT_FEEDBACK_MIN,
        "paired_efficiency_threshold": PAIRED_EFFICIENCY_MIN,
        "categories": categories,
        "experiments": len(all_pairs),
        "planned_experiments": sum(pair["status"] == "planned" for pair in all_pairs),
        "collecting_experiments": sum(
            pair["status"] == "collecting" for pair in all_pairs
        ),
        "ready_experiments": sum(pair["status"] == "ready" for pair in all_pairs),
    }


def experiment_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Plan",
        "",
        f"Proposed comparisons: **{plan['experiments']}**",
        f"Planned: **{plan.get('planned_experiments', 0)}**",
        f"Collecting: **{plan.get('collecting_experiments', 0)}**",
        f"Ready for evaluation: **{plan['ready_experiments']}**",
        f"Complete-pair quality threshold: **{plan['paired_quality_threshold']}**",
        f"Successful-pair efficiency threshold: **{plan['paired_efficiency_threshold']}**",
        "",
        (
            "Use the same logical task ID for both sides of each comparison. The planner proposes "
            "experiments only; it never executes a provider or writes feedback by itself."
        ),
        "",
    ]

    if not plan["categories"]:
        lines.extend(["No recognized activity categories were available for experiment planning.", ""])
        return "\n".join(lines)

    for category in plan["categories"]:
        lines.extend(
            [
                f"## {category['category']}",
                "",
                f"Current primary: **{_config_text_from_dict(category['primary'])}**",
                "",
            ]
        )
        for pair in sorted(category["pairs"], key=lambda item: item["priority"]):
            primary = _config_text_from_dict(pair["primary"])
            challenger = _config_text_from_dict(pair["challenger"])
            action = pair["next_action"]
            if action["type"] == "collect_paired_tasks":
                action_text = f"collect {action['paired_tasks_needed']} more complete paired task(s)"
            else:
                action_text = "evaluate the saved plan"
            lines.extend(
                [
                    f"### {pair['priority']}. {pair['kind']} comparison",
                    "",
                    f"- Status: **{pair['status']}**",
                    f"- Next action: **{action_text}**",
                    f"- Experiment ID: `{pair['experiment_id']}`",
                    f"- A: `{primary}`",
                    f"- B: `{challenger}`",
                    f"- Complete paired tasks: **{pair['complete_paired_tasks_observed']}**",
                    (
                        "- Successful paired tasks usable for efficiency: "
                        f"**{pair['successful_paired_tasks_observed']}**"
                    ),
                    (
                        "- Additional complete pairs needed for quality/evaluation: "
                        f"**{pair['quality_paired_tasks_remaining']}**"
                    ),
                    (
                        "- Additional successful pairs needed for efficiency: "
                        f"**{pair['paired_tasks_remaining']}**"
                    ),
                    (
                        "- Ambiguous/incomplete task IDs: "
                        f"**{len(pair['ambiguous_task_ids'])}/{len(pair['incomplete_task_ids'])}**"
                    ),
                    f"- Shared task ID template: `{pair['task_id_template']}`",
                    f"- Why: {pair['rationale']}",
                    "",
                ]
            )

    return "\n".join(lines)


def _config_text_from_dict(rec: dict[str, Any]) -> str:
    return f"{rec['model_id']} / {rec['effort']} / {rec['execution_mode']}"
