from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.report import recommendation_markdown

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def test_recommendation_exposes_applied_empirical_score_breakdown():
    registry = ModelRegistry(REGISTRY)
    workload = ActivityAnalyzer().from_texts(
        [
            "Implement a small backend endpoint",
            "Implement another API feature",
        ]
    )

    static_recommendations = RecommendationEngine(registry).recommend(workload, top_n=10)
    target = static_recommendations[0]
    category = max(workload.categories.items(), key=lambda item: (item[1], item[0]))[0]
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider=target.provider,
                model_id=target.model_id,
                effort=target.effort,
                execution_mode=target.execution_mode,
                outcome="success",
                task_category=category,
            )
            for _ in range(4)
        ]
    )

    personalized = RecommendationEngine(registry, feedback).recommend(workload, top_n=10)
    result = next(item for item in personalized if item.model_id == target.model_id)

    assert result.quality_adjustment > 0
    assert result.efficiency_adjustment == 0
    assert round(result.base_score + result.empirical_adjustment, 2) == result.score
    payload = result.as_dict()
    assert payload["base_score"] == result.base_score
    assert payload["quality_adjustment"] == result.quality_adjustment
    assert payload["efficiency_adjustment"] == result.efficiency_adjustment
    assert payload["empirical_adjustment"] == result.empirical_adjustment
    assert payload["raw_empirical_adjustment"] == result.raw_empirical_adjustment

    markdown = recommendation_markdown(workload, [result], registry.as_of)
    assert "Score breakdown" in markdown
    assert "applied empirical" in markdown
