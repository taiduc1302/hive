from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _profiles():
    return ActivityAnalyzer().category_profiles_from_texts(
        [
            "Implement a backend API endpoint and update tests",
            "Implement another small service feature",
            "Debug an unknown repository issue",
        ]
    )


def _plan(feedback=None):
    registry = ModelRegistry(REGISTRY)
    return build_experiment_plan(
        _profiles(),
        RecommendationEngine(registry, feedback or FeedbackStore()),
    )


def _implementation_category(plan):
    return next(item for item in plan["categories"] if item["category"] == "implementation")


def test_experiment_plan_isolates_model_effort_and_execution_pairs():
    plan = _plan()
    category = _implementation_category(plan)
    by_kind = {pair["kind"]: pair for pair in category["pairs"]}

    assert {"model", "effort", "execution"} <= set(by_kind)
    model_pair = by_kind["model"]
    effort_pair = by_kind["effort"]
    execution_pair = by_kind["execution"]

    assert model_pair["primary"]["model_id"] != model_pair["challenger"]["model_id"]

    assert effort_pair["primary"]["model_id"] == effort_pair["challenger"]["model_id"]
    assert effort_pair["primary"]["execution_mode"] == effort_pair["challenger"]["execution_mode"]
    assert effort_pair["primary"]["effort"] != effort_pair["challenger"]["effort"]

    assert execution_pair["primary"]["model_id"] == execution_pair["challenger"]["model_id"]
    assert execution_pair["primary"]["effort"] == execution_pair["challenger"]["effort"]
    assert (
        execution_pair["primary"]["execution_mode"]
        != execution_pair["challenger"]["execution_mode"]
    )

    assert all(pair["paired_tasks_remaining"] == 3 for pair in by_kind.values())
    assert all(pair["status"] == "planned" for pair in by_kind.values())
    assert plan["planned_experiments"] == plan["experiments"]
    assert plan["collecting_experiments"] == 0
    assert plan["ready_experiments"] == 0
    assert [pair["priority"] for pair in category["pairs"]] == [1, 2, 3]


def test_experiment_ids_are_deterministic_for_same_ranking():
    first = _plan()
    second = _plan()
    first_pairs = [
        pair["experiment_id"]
        for category in first["categories"]
        for pair in category["pairs"]
    ]
    second_pairs = [
        pair["experiment_id"]
        for category in second["categories"]
        for pair in category["pairs"]
    ]
    assert first_pairs == second_pairs


def test_existing_shared_successful_task_reduces_remaining_pair_count():
    initial = _plan()
    pair = next(
        item
        for item in _implementation_category(initial)["pairs"]
        if item["kind"] == "model"
    )
    task_id = pair["task_id_template"].format(nn="01")
    records = []
    for side in (pair["primary"], pair["challenger"]):
        records.append(
            UsageRecord(
                provider=side["provider"],
                model_id=side["model_id"],
                effort=side["effort"],
                execution_mode=side["execution_mode"],
                outcome="success",
                latency_seconds=10,
                cost_usd=0.10,
                task_category="implementation",
                task_id=task_id,
            )
        )

    updated = _plan(FeedbackStore(records))
    updated_pair = next(
        item
        for item in _implementation_category(updated)["pairs"]
        if item["kind"] == "model"
    )
    assert updated_pair["shared_successful_task_ids"] == [task_id]
    assert updated_pair["paired_tasks_observed"] == 1
    assert updated_pair["paired_tasks_remaining"] == 2
    assert updated_pair["efficiency_ready"] is False
    assert updated_pair["status"] == "collecting"
    assert updated["collecting_experiments"] >= 1


def test_success_in_different_category_does_not_count_as_shared_pair():
    initial = _plan()
    pair = next(
        item
        for item in _implementation_category(initial)["pairs"]
        if item["kind"] == "model"
    )
    task_id = pair["task_id_template"].format(nn="01")
    records = [
        UsageRecord(
            provider=side["provider"],
            model_id=side["model_id"],
            effort=side["effort"],
            execution_mode=side["execution_mode"],
            outcome="success",
            latency_seconds=10,
            cost_usd=0.10,
            task_category="research",
            task_id=task_id,
        )
        for side in (pair["primary"], pair["challenger"])
    ]

    updated = _plan(FeedbackStore(records))
    updated_pair = next(
        item
        for item in _implementation_category(updated)["pairs"]
        if item["kind"] == "model"
    )
    assert updated_pair["paired_tasks_observed"] == 0
    assert updated_pair["paired_tasks_remaining"] == 3
    assert updated_pair["status"] == "planned"
