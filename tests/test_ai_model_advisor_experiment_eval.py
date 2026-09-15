from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.experiment_eval import (
    evaluate_experiment_plan,
    experiment_evaluation_markdown,
)
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _plan():
    profiles = ActivityAnalyzer().category_profiles_from_texts(
        [
            "Implement a backend API endpoint and update tests",
            "Implement another small service feature",
            "Debug an unknown repository issue",
        ]
    )
    registry = ModelRegistry(REGISTRY)
    return build_experiment_plan(profiles, RecommendationEngine(registry))


def _implementation_pair(plan, kind="model"):
    category = next(
        item for item in plan["categories"] if item["category"] == "implementation"
    )
    return next(pair for pair in category["pairs"] if pair["kind"] == kind)


def _record(
    side,
    task_id,
    outcome,
    *,
    latency=None,
    cost=None,
    retries=0,
):
    return UsageRecord(
        provider=side["provider"],
        model_id=side["model_id"],
        effort=side["effort"],
        execution_mode=side["execution_mode"],
        outcome=outcome,
        retries=retries,
        latency_seconds=latency,
        cost_usd=cost,
        task_category="implementation",
        task_id=task_id,
    )


def _result(report, experiment_id):
    return next(
        item for item in report["results"] if item["experiment_id"] == experiment_id
    )


def test_experiment_evaluation_reports_planned_without_pairs():
    plan = _plan()
    pair = _implementation_pair(plan)
    report = evaluate_experiment_plan(plan, FeedbackStore())
    result = _result(report, pair["experiment_id"])

    assert result["paired_tasks"] == 0
    assert result["decision"] == "insufficient_evidence"
    assert result["confidence"] == "insufficient"
    assert result["policy_ready"] is False
    assert result["status"] == "planned"
    assert result["next_action"] == {
        "type": "collect_paired_tasks",
        "paired_tasks_needed": 3,
    }
    assert report["policy_ready_experiments"] == 0
    assert report["status_counts"]["planned"] == report["experiments"]
    assert report["evaluations"] == report["results"]


def test_experiment_evaluation_marks_partial_collection_as_collecting():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(2):
        task_id = pair["task_id_template"].format(nn=f"{index:02d}")
        records.extend(
            [
                _record(pair["primary"], task_id, "success", latency=10, cost=0.10),
                _record(
                    pair["challenger"],
                    task_id,
                    "success",
                    latency=10,
                    cost=0.10,
                ),
            ]
        )

    result = _result(
        evaluate_experiment_plan(plan, FeedbackStore(records)),
        pair["experiment_id"],
    )
    assert result["paired_tasks"] == 2
    assert result["status"] == "collecting"
    assert result["decision"] == "insufficient_evidence"
    assert result["additional_paired_tasks_needed"] == 1
    assert result["next_action"] == {
        "type": "collect_paired_tasks",
        "paired_tasks_needed": 1,
    }


def test_experiment_evaluation_prefers_outcome_quality_before_efficiency():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(3):
        task_id = pair["task_id_template"].format(nn=f"{index:02d}")
        records.extend(
            [
                _record(
                    pair["primary"],
                    task_id,
                    "partial",
                    latency=5,
                    cost=0.05,
                ),
                _record(
                    pair["challenger"],
                    task_id,
                    "success",
                    latency=20,
                    cost=0.20,
                ),
            ]
        )

    report = evaluate_experiment_plan(plan, FeedbackStore(records))
    result = _result(report, pair["experiment_id"])

    assert result["paired_tasks"] == 3
    assert result["challenger_quality_wins"] == 3
    assert result["decision"] == "challenger_leads"
    assert result["decision_basis"] == "outcome_quality"
    assert result["winner_side"] == "challenger"
    assert result["policy_ready"] is True
    assert result["status"] == "decided"
    assert result["next_action"] == {
        "type": "review_winner_and_rerun_router",
        "paired_tasks_needed": 0,
        "winner_side": "challenger",
    }
    assert result["confidence"] in {"medium", "high"}


def test_experiment_evaluation_uses_efficiency_only_after_successful_quality_tie():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(3):
        task_id = pair["task_id_template"].format(nn=f"{index:02d}")
        records.extend(
            [
                _record(
                    pair["primary"],
                    task_id,
                    "success",
                    latency=10,
                    cost=0.10,
                ),
                _record(
                    pair["challenger"],
                    task_id,
                    "success",
                    latency=20,
                    cost=0.20,
                ),
            ]
        )

    report = evaluate_experiment_plan(plan, FeedbackStore(records))
    result = _result(report, pair["experiment_id"])

    assert result["quality_ties"] == 3
    assert result["efficiency_paired_tasks"] == 3
    assert result["efficiency_delta_primary_advantage"] > 0
    assert result["decision"] == "primary_leads"
    assert result["decision_basis"] == "paired_efficiency"
    assert result["winner_side"] == "primary"
    assert result["policy_ready"] is True
    assert result["status"] == "decided"


def test_conflicting_retry_and_efficiency_signals_become_tradeoff():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(3):
        task_id = pair["task_id_template"].format(nn=f"{index:02d}")
        records.extend(
            [
                _record(
                    pair["primary"],
                    task_id,
                    "success",
                    latency=30,
                    cost=0.30,
                    retries=0,
                ),
                _record(
                    pair["challenger"],
                    task_id,
                    "success",
                    latency=10,
                    cost=0.10,
                    retries=1,
                ),
            ]
        )

    result = _result(
        evaluate_experiment_plan(plan, FeedbackStore(records)),
        pair["experiment_id"],
    )
    assert result["retry_leader"] == "A"
    assert result["weighted_efficiency_leader"] == "B"
    assert result["decision"] == "tradeoff"
    assert result["status"] == "tradeoff"
    assert result["winner_side"] is None
    assert result["next_action"] == {
        "type": "review_tradeoff_or_collect_more",
        "paired_tasks_needed": 1,
    }


def test_experiment_evaluation_ignores_same_configs_outside_experiment_prefix():
    plan = _plan()
    pair = _implementation_pair(plan)
    records = []
    for index in range(3):
        task_id = f"implementation-unrelated-task-{index:02d}"
        records.extend(
            [
                _record(pair["primary"], task_id, "success", latency=10, cost=0.10),
                _record(
                    pair["challenger"],
                    task_id,
                    "success",
                    latency=20,
                    cost=0.20,
                ),
            ]
        )

    report = evaluate_experiment_plan(plan, FeedbackStore(records))
    result = _result(report, pair["experiment_id"])

    assert result["paired_tasks"] == 0
    assert result["decision"] == "insufficient_evidence"
    assert result["status"] == "planned"


def test_experiment_evaluation_markdown_disclaims_statistical_probability():
    plan = _plan()
    report = evaluate_experiment_plan(plan, FeedbackStore())
    markdown = experiment_evaluation_markdown(report)

    assert "not a statistical probability" in markdown
    assert "Experiment details" in markdown
    assert "Planned:" in markdown
    assert "Next action:" in markdown
