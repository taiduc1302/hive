from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .execution_targets import (
    ExecutionTargetProfile,
    configuration_blockers,
    profile_for_host,
)
from .models import ModelProfile, Recommendation, WorkloadProfile
from .recommend import RecommendationEngine


def _configuration_allowed(
    model: ModelProfile,
    effort: str,
    mode: str,
    profile: ExecutionTargetProfile,
) -> bool:
    return not configuration_blockers(
        {
            "provider": model.provider,
            "model_id": model.model_id,
            "effort": effort,
            "execution_mode": mode,
        },
        profile,
    )


def _allowed_modes(
    model: ModelProfile,
    profile: ExecutionTargetProfile,
) -> tuple[str, ...]:
    return tuple(
        mode
        for mode in model.execution_modes
        if _configuration_allowed(model, model.default_effort or "default", mode, profile)
    )


def _best_target_configuration(
    engine: RecommendationEngine,
    model: ModelProfile,
    workload: WorkloadProfile,
    task_category: str | None,
    profile: ExecutionTargetProfile,
) -> Recommendation | None:
    allowed_modes = _allowed_modes(model, profile)
    if not allowed_modes:
        return None

    restricted_model = replace(model, execution_modes=allowed_modes)
    preferred_effort = engine._effort(model, workload)
    preferred_mode = engine._execution_mode(restricted_model, workload)

    configurations = [
        engine._score_configuration(
            model,
            workload,
            effort,
            mode,
            preferred_effort,
            preferred_mode,
            task_category,
        )
        for effort in model.efforts
        for mode in allowed_modes
        if _configuration_allowed(model, effort, mode, profile)
    ]
    if not configurations:
        return None

    return max(
        configurations,
        key=lambda item: (
            item.score,
            item.configuration_adjustment,
            item.effort == preferred_effort,
            item.execution_mode == preferred_mode,
        ),
    )


def recommend_for_target(
    engine: RecommendationEngine,
    workload: WorkloadProfile,
    execution_target: str,
    *,
    providers: Iterable[str] | None = None,
    include_limited: bool = False,
    top_n: int = 3,
) -> list[Recommendation]:
    """Rank only configurations the selected execution target can run.

    Generic Advisor routing intentionally remains host-agnostic. This helper
    narrows the configuration space before scoring, then recomputes each
    model's preferred execution mode inside that executable subset. That avoids
    recommending an unavailable orchestration mode and only discovering the
    mismatch later during experiment preflight.
    """

    profile = profile_for_host(execution_target)
    requested_providers = set(providers or ())
    allowed_providers = set(profile.supported_providers)
    if requested_providers:
        providers = tuple(sorted(requested_providers & allowed_providers))
        if not providers:
            return []
    else:
        providers = profile.supported_providers

    task_category = engine._primary_category(workload)
    scored: list[Recommendation] = []
    for model in engine.registry.candidates(
        providers=providers,
        include_limited=include_limited,
    ):
        recommendation = _best_target_configuration(
            engine,
            model,
            workload,
            task_category,
            profile,
        )
        if recommendation is not None:
            scored.append(recommendation)

    scored.sort(key=lambda item: item.score, reverse=True)
    if not scored:
        return []

    best = scored[0].score
    second = scored[1].score if len(scored) > 1 else best - 5
    spread = max(1.0, abs(best - second))
    activity_factor = min(1.0, 0.35 + workload.activity_count / 25.0)
    confidence = min(0.96, 0.58 + min(0.25, spread / 40.0) + 0.13 * activity_factor)
    return [
        replace(item, confidence=round(max(0.45, confidence - index * 0.08), 2))
        for index, item in enumerate(scored[:top_n])
    ]
