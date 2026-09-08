from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.experiment_impact import (
    build_experiment_impact,
    experiment_impact_markdown,
)
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _registry():
    return ModelRegistry(REGISTRY)


def _plan():
    profiles = ActivityAnalyzer().category_profiles_from_texts(
        [
            "Implement a backend API endpoint and update tests",
            "Implement another small service feature",
            "Debug an unknown repository issue",
        ]
    )
    registry = _registry()
    return build_experiment_plan(profiles, RecommendationEngine(registry))


def _implementation_pair(plan, kind="model"):
    category = next(
        item for item in plan["categories"] if item["category"] == "implementation"
    )
    return next(pair for pair in category["pairs"] if pair["kind"] == kind)


def _record(side, task_id, outcome):
    return UsageRecord(
        provider=side["provider"],
        model_id=side["model_id"],
        effort=side["effort"],
        execution_mode=side["execution_mode"],
        outcome=outcome,
        latency_seconds=10,
        cost_usd=0.10,
        task_category="implementation",
        task_id=task_id,
    )


def _impact(report, experiment_id):
    return next(
        item for item in report["impacts"] if item["experiment_id"] == experiment_id
    )


def test_experiment_impact_does_not_call_undecided_evidence_a_router_gap():
    plan = _plan()
    pair = _implementation_pair(plan)
    report = build_experiment_impact(plan, FeedbackStore(), _registry())
    impact = _impact(report, pair["experiment_id"])

    assert report["decided_experiments"] == 0
    assert report["aligned_decided_experiments"] == 0
    assert report["misaligned_decided_experiments"] == 0
    assert impact["experiment_decision"] == "insufficient_evidence"
    assert impact["impact_status"] == "not_decided"
    assert impact["router_aligned"] is False
    assert impact["experiment_winner"] is None
    assert impact["winner_current_score"] is None
    assert impact["current_router"] is not None
    assert impact["next_action"]["type"] == "collect_paired_tasks"


def test_experiment_impact_rescores_a_decided_winner_against_live_router():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(3):
        task_id = pair["task_id_template"].format(nn=f"{index:02d}")
        records.extend(
            [
                _record(pair["primary"], task_id, "partial"),
                _record(pair["challenger"], task_id, "success"),
            ]
        )

    report = build_experiment_impact(plan, FeedbackStore(records), _registry())
    impact = _impact(report, pair["experiment_id"])

    assert impact["experiment_decision"] == "challenger_leads"
    assert impact["decision_basis"] == "outcome_quality"
    assert impact["experiment_winner"]["model_id"] == pair["challenger"]["model_id"]
    assert impact["winner_current_score"] is not None
    assert impact["loser_current_score"] is not None
    assert impact["current_router"] is not None
    assert isinstance(impact["winner_score_gap_to_current"], float)
    assert impact["impact_status"] in {
        "aligned",
        "still_on_experiment_loser",
        "same_winner_model_different_configuration",
        "same_loser_model_different_configuration",
        "different_primary",
    }
    if impact["router_aligned"]:
        assert impact["impact_status"] == "aligned"
        assert impact["winner_score_gap_to_current"] == 0
        assert impact["next_action"]["type"] == "observe_and_rerun_later"
    else:
        assert impact["next_action"]["type"] == "review_router_gap"


def test_experiment_impact_markdown_is_explicitly_read_only():
    plan = _plan()
    report = build_experiment_impact(plan, FeedbackStore(), _registry())
    markdown = experiment_impact_markdown(report)

    assert "Experiment Impact" in markdown
    assert "read-only" in markdown
    assert "Router-gap" in markdown
