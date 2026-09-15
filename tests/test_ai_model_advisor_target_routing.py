from __future__ import annotations

from pathlib import Path

import pytest

from tools.ai_model_advisor.execution_targets import ExecutionTargetCatalogError
from tools.ai_model_advisor.models import WorkloadProfile
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.target_routing import recommend_for_target

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _broad_parallel_workload() -> WorkloadProfile:
    return WorkloadProfile(
        coding=4.5,
        reasoning=4.2,
        agentic=4.6,
        ambiguity=3.8,
        breadth=5.0,
        parallelism=5.0,
        latency_sensitivity=2.0,
        cost_sensitivity=2.0,
        volume=3.0,
        categories={"implementation": 30},
        activity_count=30,
    )


def _engine() -> RecommendationEngine:
    return RecommendationEngine(ModelRegistry(REGISTRY))


def test_generic_routing_can_still_recommend_orchestration() -> None:
    recommendations = _engine().recommend(
        _broad_parallel_workload(),
        providers=["anthropic"],
        top_n=4,
    )

    assert recommendations
    assert any(item.execution_mode != "single" for item in recommendations)


def test_hive_target_recomputes_best_configuration_inside_executable_modes() -> None:
    recommendations = recommend_for_target(
        _engine(),
        _broad_parallel_workload(),
        "hive",
        providers=["anthropic"],
        top_n=4,
    )

    assert recommendations
    assert all(item.execution_mode == "single" for item in recommendations)
    assert any(item.model_id == "claude-sonnet-5" for item in recommendations)
    assert all(item.preferred_execution_mode == "single" for item in recommendations)


def test_direct_provider_target_only_emits_executable_single_calls() -> None:
    recommendations = recommend_for_target(
        _engine(),
        _broad_parallel_workload(),
        "provider_api",
        top_n=8,
    )

    assert recommendations
    assert all(item.execution_mode == "single" for item in recommendations)
    assert {item.provider for item in recommendations} <= {"openai", "anthropic"}


def test_target_provider_filter_intersects_with_target_capabilities() -> None:
    recommendations = recommend_for_target(
        _engine(),
        _broad_parallel_workload(),
        "provider_api",
        providers=["anthropic"],
        top_n=8,
    )

    assert recommendations
    assert {item.provider for item in recommendations} == {"anthropic"}


def test_haiku_provider_default_effort_survives_target_filter() -> None:
    workload = WorkloadProfile(
        coding=1.0,
        reasoning=1.0,
        agentic=1.0,
        ambiguity=1.0,
        breadth=1.0,
        parallelism=1.0,
        latency_sensitivity=5.0,
        cost_sensitivity=5.0,
        volume=5.0,
        categories={"routine": 20},
        activity_count=20,
    )
    recommendations = recommend_for_target(
        _engine(),
        workload,
        "provider_api",
        providers=["anthropic"],
        top_n=4,
    )
    haiku = next(item for item in recommendations if item.model_id == "claude-haiku-4-5-20251001")

    assert haiku.effort == "default"
    assert haiku.execution_mode == "single"


def test_unknown_execution_target_is_rejected() -> None:
    with pytest.raises(ExecutionTargetCatalogError):
        recommend_for_target(_engine(), _broad_parallel_workload(), "missing")
