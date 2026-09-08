from __future__ import annotations

from typing import Any

from .experiment_eval import evaluate_experiment_plan
from .feedback import FeedbackStore
from .models import ModelProfile, Recommendation, WorkloadProfile
from .recommend import RecommendationEngine
from .registry import ModelRegistry

_DIRECTIONAL_DECISIONS = {"primary_leads", "challenger_leads"}


def _config_key(config: dict[str, Any] | Recommendation) -> tuple[str, str, str]:
    if isinstance(config, Recommendation):
        return config.model_id, config.effort, config.execution_mode
    return (
        str(config.get("model_id") or ""),
        str(config.get("effort") or ""),
        str(config.get("execution_mode") or ""),
    )


def _config_text(config: dict[str, Any] | Recommendation) -> str:
    return " / ".join(_config_key(config))


def _workload_from_category(category: dict[str, Any]) -> WorkloadProfile:
    data = category.get("workload") or {}
    allowed = set(WorkloadProfile.__dataclass_fields__)
    return WorkloadProfile(
        **{key: value for key, value in data.items() if key in allowed}
    )


def _find_model(registry: ModelRegistry, model_id: str) -> ModelProfile | None:
    matches = [model for model in registry.models if model.model_id == model_id]
    return matches[0] if len(matches) == 1 else None


def _score_saved_config(
    engine: RecommendationEngine,
    workload: WorkloadProfile,
    category: str,
    config: dict[str, Any],
) -> Recommendation | None:
    model = _find_model(engine.registry, str(config.get("model_id") or ""))
    if model is None:
        return None
    effort = str(config.get("effort") or "")
    mode = str(config.get("execution_mode") or "")
    if effort not in model.efforts or mode not in model.execution_modes:
        return None
    preferred_effort = engine._effort(model, workload)
    preferred_mode = engine._execution_mode(model, workload)
    return engine._score_configuration(
        model,
        workload,
        effort,
        mode,
        preferred_effort,
        preferred_mode,
        category,
    )


def _alignment_status(
    current: Recommendation,
    winner: dict[str, Any],
    loser: dict[str, Any],
) -> str:
    current_key = _config_key(current)
    winner_key = _config_key(winner)
    loser_key = _config_key(loser)
    if current_key == winner_key:
        return "aligned"
    if current_key == loser_key:
        return "still_on_experiment_loser"
    if current.model_id == winner_key[0]:
        return "same_winner_model_different_configuration"
    if current.model_id == loser_key[0]:
        return "same_loser_model_different_configuration"
    return "different_primary"


def _next_action(
    impact_status: str,
    evaluation: dict[str, Any],
    score_gap: float | None,
) -> dict[str, Any]:
    if impact_status == "not_decided":
        return dict(evaluation.get("next_action") or {"type": "collect_more_evidence"})
    if impact_status == "aligned":
        return {"type": "observe_and_rerun_later"}
    if impact_status == "winner_unavailable":
        return {"type": "refresh_registry_or_plan"}
    if impact_status == "no_router_candidate":
        return {"type": "review_provider_filters_or_registry"}
    return {
        "type": "review_router_gap",
        "winner_score_gap_to_current": score_gap,
    }


def build_experiment_impact(
    plan: dict[str, Any],
    feedback: FeedbackStore,
    registry: ModelRegistry,
    providers: list[str] | None = None,
    include_limited: bool = False,
) -> dict[str, Any]:
    """Compare fixed-plan experiment decisions with the current live router.

    This report is read-only. It does not promote a winner or write feedback.
    A decided experiment can disagree with the live router because the router
    also considers the full model field, static capability fit, configuration
    priors, other empirical evidence, and current registry availability.
    """
    engine = RecommendationEngine(registry, feedback)
    evaluation = evaluate_experiment_plan(plan, feedback)
    categories = {
        str(category.get("category") or ""): category
        for category in plan.get("categories", [])
    }
    impacts: list[dict[str, Any]] = []

    for result in evaluation["results"]:
        category_name = str(result["category"])
        category = categories.get(category_name, {})
        workload = _workload_from_category(category)
        current_rows = engine.recommend(
            workload,
            providers=providers,
            include_limited=include_limited,
            top_n=1,
        )
        current = current_rows[0] if current_rows else None

        base = {
            "experiment_id": result["experiment_id"],
            "kind": result["kind"],
            "category": category_name,
            "experiment_status": result["status"],
            "experiment_decision": result["decision"],
            "decision_basis": result["decision_basis"],
            "confidence": result["confidence"],
            "confidence_score": result["confidence_score"],
            "policy_ready": result["policy_ready"],
            "current_router": current.as_dict() if current else None,
        }

        if result["decision"] not in _DIRECTIONAL_DECISIONS:
            impact_status = "not_decided"
            impacts.append(
                {
                    **base,
                    "impact_status": impact_status,
                    "router_aligned": False,
                    "experiment_winner": None,
                    "experiment_loser": None,
                    "winner_current_score": None,
                    "loser_current_score": None,
                    "winner_score_gap_to_current": None,
                    "next_action": _next_action(impact_status, result, None),
                }
            )
            continue

        winner_side = str(result["winner_side"])
        loser_side = "challenger" if winner_side == "primary" else "primary"
        winner = result[winner_side]
        loser = result[loser_side]
        winner_scored = _score_saved_config(
            engine,
            workload,
            category_name,
            winner,
        )
        loser_scored = _score_saved_config(
            engine,
            workload,
            category_name,
            loser,
        )

        if winner_scored is None:
            impact_status = "winner_unavailable"
            gap = None
        elif current is None:
            impact_status = "no_router_candidate"
            gap = None
        else:
            impact_status = _alignment_status(current, winner, loser)
            gap = round(winner_scored.score - current.score, 3)

        impacts.append(
            {
                **base,
                "impact_status": impact_status,
                "router_aligned": impact_status == "aligned",
                "experiment_winner": winner,
                "experiment_loser": loser,
                "winner_current_score": (
                    winner_scored.as_dict() if winner_scored else None
                ),
                "loser_current_score": loser_scored.as_dict() if loser_scored else None,
                "winner_score_gap_to_current": gap,
                "next_action": _next_action(impact_status, result, gap),
            }
        )

    decided_impacts = [
        impact
        for impact in impacts
        if impact["experiment_decision"] in _DIRECTIONAL_DECISIONS
    ]
    return {
        "registry_as_of": registry.as_of,
        "experiments": len(impacts),
        "decided_experiments": len(decided_impacts),
        "aligned_decided_experiments": sum(
            impact["router_aligned"] for impact in decided_impacts
        ),
        "misaligned_decided_experiments": sum(
            not impact["router_aligned"] for impact in decided_impacts
        ),
        "note": (
            "Impact analysis is read-only. A fixed A/B winner is evidence, not an "
            "automatic policy override; the live router may still prefer another "
            "configuration after considering the full scoring field."
        ),
        "impacts": impacts,
    }


def experiment_impact_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Impact",
        "",
        f"Registry: **{report['registry_as_of']}**",
        f"Experiments checked: **{report['experiments']}**",
        f"Decided experiments: **{report['decided_experiments']}**",
        f"Router-aligned decided experiments: **{report['aligned_decided_experiments']}**",
        f"Router-gap decided experiments: **{report['misaligned_decided_experiments']}**",
        "",
        report["note"],
        "",
    ]
    if not report["impacts"]:
        lines.extend(["No experiments were present in the saved plan.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "| Category | Kind | Experiment | Impact | Current router | Winner gap | Next action |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    for impact in report["impacts"]:
        current = impact["current_router"]
        current_text = _config_text(current) if current else "—"
        gap = impact["winner_score_gap_to_current"]
        gap_text = f"{gap:+.3f}" if gap is not None else "—"
        action = str(impact["next_action"].get("type") or "review").replace("_", " ")
        lines.append(
            f"| {impact['category']} | {impact['kind']} | {impact['experiment_decision']} | "
            f"{impact['impact_status']} | `{current_text}` | {gap_text} | {action} |"
        )

    lines.extend(["", "## Decided experiment details", ""])
    for impact in report["impacts"]:
        if impact["experiment_decision"] not in _DIRECTIONAL_DECISIONS:
            continue
        winner = impact["experiment_winner"]
        current = impact["current_router"]
        lines.extend(
            [
                f"### {impact['category']} / {impact['kind']} / {impact['experiment_id']}",
                "",
                f"- Experiment winner: `{_config_text(winner)}`",
                f"- Current router: `{_config_text(current) if current else 'none'}`",
                f"- Alignment: **{impact['impact_status']}**",
                f"- Experiment confidence: **{impact['confidence']} ({impact['confidence_score']:.2f})**",
                f"- Decision basis: `{impact['decision_basis']}`",
            ]
        )
        winner_score = impact["winner_current_score"]
        if winner_score:
            lines.extend(
                [
                    f"- Winner current score: **{winner_score['score']:.2f}**",
                    (
                        "- Winner score layers: model "
                        f"**{winner_score['model_score']:.2f}**; configuration "
                        f"**{winner_score['configuration_adjustment']:+.3f}**; empirical "
                        f"**{winner_score['empirical_adjustment']:+.3f}**"
                    ),
                ]
            )
        if impact["winner_score_gap_to_current"] is not None:
            lines.append(
                "- Winner minus current-router score: "
                f"**{impact['winner_score_gap_to_current']:+.3f}**"
            )
        lines.append(
            "- Next action: `"
            + str(impact["next_action"].get("type") or "review")
            + "`"
        )
        lines.append("")

    return "\n".join(lines)
