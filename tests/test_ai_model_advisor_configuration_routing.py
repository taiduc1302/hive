from pathlib import Path

from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.models import WorkloadProfile
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _implementation_workload() -> WorkloadProfile:
    return WorkloadProfile(
        coding=3.0,
        reasoning=2.5,
        agentic=1.5,
        ambiguity=2.0,
        breadth=1.5,
        parallelism=1.5,
        latency_sensitivity=3.0,
        cost_sensitivity=3.0,
        categories={"implementation": 6},
        activity_count=6,
    )


def _by_model(recommendations, model_id):
    return next(item for item in recommendations if item.model_id == model_id)


def test_static_routing_keeps_heuristic_configuration_and_model_diversity():
    registry = ModelRegistry(REGISTRY)
    workload = _implementation_workload()
    engine = RecommendationEngine(registry)
    recommendations = engine.recommend(workload, top_n=10)

    assert len({item.model_id for item in recommendations}) == len(recommendations)
    terra = _by_model(recommendations, "gpt-5.6-terra")
    sonnet = _by_model(recommendations, "claude-sonnet-5")
    assert terra.effort == "medium"
    assert terra.execution_mode == "single"
    assert sonnet.effort == "medium"
    assert sonnet.execution_mode == "single"
    assert terra.configuration_adjustment == 0
    assert terra.base_score == terra.model_score


def test_exact_effort_history_can_override_static_effort_prior():
    registry = ModelRegistry(REGISTRY)
    workload = _implementation_workload()
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="openai",
                model_id="gpt-5.6-terra",
                effort="high",
                execution_mode="single",
                outcome="success",
                task_category="implementation",
            )
            for _ in range(6)
        ]
    )

    terra = _by_model(
        RecommendationEngine(registry, feedback).recommend(
            workload,
            providers=["openai"],
            top_n=10,
        ),
        "gpt-5.6-terra",
    )

    assert terra.preferred_effort == "medium"
    assert terra.effort == "high"
    assert terra.execution_mode == "single"
    assert terra.configuration_adjustment < 0
    assert terra.quality_adjustment > 0
    assert terra.quality_adjustment > abs(terra.configuration_adjustment)
    assert round(terra.base_score + terra.empirical_adjustment, 2) == terra.score


def test_exact_execution_history_can_override_static_execution_prior():
    registry = ModelRegistry(REGISTRY)
    workload = _implementation_workload()
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="anthropic",
                model_id="claude-sonnet-5",
                effort="medium",
                execution_mode="subagents",
                outcome="success",
                task_category="implementation",
            )
            for _ in range(6)
        ]
    )

    sonnet = _by_model(
        RecommendationEngine(registry, feedback).recommend(
            workload,
            providers=["anthropic"],
            top_n=10,
        ),
        "claude-sonnet-5",
    )

    assert sonnet.preferred_execution_mode == "single"
    assert sonnet.effort == "medium"
    assert sonnet.execution_mode == "subagents"
    assert sonnet.configuration_adjustment < 0
    assert sonnet.quality_adjustment > 0
    assert sonnet.quality_adjustment > abs(sonnet.configuration_adjustment)


def test_configuration_prior_is_exposed_separately_from_model_score():
    registry = ModelRegistry(REGISTRY)
    workload = _implementation_workload()
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="openai",
                model_id="gpt-5.6-terra",
                effort="high",
                execution_mode="single",
                outcome="success",
                task_category="implementation",
            )
            for _ in range(6)
        ]
    )
    terra = _by_model(
        RecommendationEngine(registry, feedback).recommend(workload, top_n=10),
        "gpt-5.6-terra",
    )
    payload = terra.as_dict()

    assert payload["model_score"] == terra.model_score
    assert payload["configuration_adjustment"] == terra.configuration_adjustment
    assert round(terra.model_score + terra.configuration_adjustment, 2) == terra.base_score
    assert payload["preferred_effort"] == "medium"
    assert payload["preferred_execution_mode"] == "single"
