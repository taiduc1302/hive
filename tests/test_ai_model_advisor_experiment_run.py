import hashlib
from pathlib import Path

import pytest

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.experiment_run import (
    ExperimentRunnerError,
    RunnerInfrastructureError,
    append_pair_feedback,
    command_executor,
    run_experiment_pair,
)
from tools.ai_model_advisor.feedback import FeedbackStore
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _plan(feedback=None):
    profiles = ActivityAnalyzer().category_profiles_from_texts(
        [
            "Implement a backend API endpoint and update tests",
            "Implement another small service feature",
        ]
    )
    registry = ModelRegistry(REGISTRY)
    return build_experiment_plan(
        profiles,
        RecommendationEngine(registry, feedback or FeedbackStore()),
    )


def _model_pair(plan):
    category = next(item for item in plan["categories"] if item["category"] == "implementation")
    return next(pair for pair in category["pairs"] if pair["kind"] == "model")


def _success_executor(payload):
    side = payload["side"]
    return {
        "schema_version": 1,
        "applied_configuration": payload["configuration"],
        "outcome": "success",
        "retries": 0,
        "latency_seconds": 10 if side == "A" else 20,
        "cost_usd": 0.10 if side == "A" else 0.20,
        "input_tokens": 100,
        "output_tokens": 50,
        "note": f"completed side {side}",
    }


def test_runner_creates_exact_paired_records_from_saved_plan():
    plan = _plan()
    pair = _model_pair(plan)
    task = "Implement the same endpoint from this fixed benchmark fixture."
    report = run_experiment_pair(
        plan,
        FeedbackStore(),
        pair["experiment_id"],
        task,
        _success_executor,
    )

    assert report.task_id.endswith("01")
    assert report.task_sha256 == hashlib.sha256(task.encode("utf-8")).hexdigest()
    assert report.order == ("A", "B")
    assert len(report.records) == 2
    primary, challenger = report.records
    assert primary.task_id == challenger.task_id == report.task_id
    assert primary.model_id == pair["primary"]["model_id"]
    assert challenger.model_id == pair["challenger"]["model_id"]
    assert primary.effort == pair["primary"]["effort"]
    assert challenger.execution_mode == pair["challenger"]["execution_mode"]
    assert primary.source_id == f"benchmark:{pair['experiment_id']}:{report.task_id}:a"
    assert challenger.source_id == f"benchmark:{pair['experiment_id']}:{report.task_id}:b"


def test_auto_order_alternates_between_consecutive_tasks():
    initial = _plan()
    pair = _model_pair(initial)
    first = run_experiment_pair(
        initial,
        FeedbackStore(),
        pair["experiment_id"],
        "task one",
        _success_executor,
    )
    store = FeedbackStore(first.records)
    second_plan = _plan(store)
    second = run_experiment_pair(
        second_plan,
        store,
        pair["experiment_id"],
        "task two",
        _success_executor,
    )

    assert first.task_id.endswith("01")
    assert first.order == ("A", "B")
    assert second.task_id.endswith("02")
    assert second.order == ("B", "A")


def test_runner_stages_no_pair_when_adapter_fails_mid_run():
    plan = _plan()
    pair = _model_pair(plan)
    calls = []

    def executor(payload):
        calls.append(payload["side"])
        if payload["side"] == "B":
            raise RunnerInfrastructureError("synthetic adapter outage")
        return {
            "schema_version": 1,
            "applied_configuration": payload["configuration"],
            "outcome": "success",
            "latency_seconds": 1.0,
        }

    with pytest.raises(RunnerInfrastructureError, match="adapter outage"):
        run_experiment_pair(
            plan,
            FeedbackStore(),
            pair["experiment_id"],
            "fixed benchmark",
            executor,
        )

    assert calls == ["A", "B"]


def test_adapter_configuration_mismatch_is_infrastructure_failure():
    plan = _plan()
    pair = _model_pair(plan)

    def executor(payload):
        wrong = dict(payload["configuration"])
        wrong["effort"] = "ignored-by-adapter"
        return {
            "schema_version": 1,
            "applied_configuration": wrong,
            "outcome": "success",
        }

    with pytest.raises(RunnerInfrastructureError, match="does not match"):
        run_experiment_pair(
            plan,
            FeedbackStore(),
            pair["experiment_id"],
            "fixed benchmark",
            executor,
        )


def test_missing_configuration_echo_is_infrastructure_failure():
    plan = _plan()
    pair = _model_pair(plan)

    def executor(_payload):
        return {"schema_version": 1, "outcome": "success"}

    with pytest.raises(RunnerInfrastructureError, match="applied_configuration"):
        run_experiment_pair(
            plan,
            FeedbackStore(),
            pair["experiment_id"],
            "fixed benchmark",
            executor,
        )


def test_append_pair_feedback_writes_both_records_together(tmp_path):
    plan = _plan()
    pair = _model_pair(plan)
    report = run_experiment_pair(
        plan,
        FeedbackStore(),
        pair["experiment_id"],
        "fixed benchmark",
        _success_executor,
    )
    feedback_path = tmp_path / "feedback.jsonl"
    append_pair_feedback(feedback_path, report)

    loaded = FeedbackStore.load(feedback_path)
    assert len(loaded.records) == 2
    assert {record.source_id for record in loaded.records} == {
        f"benchmark:{pair['experiment_id']}:{report.task_id}:a",
        f"benchmark:{pair['experiment_id']}:{report.task_id}:b",
    }


def test_append_rechecks_duplicate_source_ids_at_write_time(tmp_path):
    plan = _plan()
    pair = _model_pair(plan)
    report = run_experiment_pair(
        plan,
        FeedbackStore(),
        pair["experiment_id"],
        "fixed benchmark",
        _success_executor,
    )
    feedback_path = tmp_path / "feedback.jsonl"
    FeedbackStore.append(feedback_path, report.records[0])

    with pytest.raises(ExperimentRunnerError, match="appeared before append"):
        append_pair_feedback(feedback_path, report)
    assert len(FeedbackStore.load(feedback_path).records) == 1


def test_existing_benchmark_source_id_is_rejected():
    plan = _plan()
    pair = _model_pair(plan)
    first = run_experiment_pair(
        plan,
        FeedbackStore(),
        pair["experiment_id"],
        "fixed benchmark",
        _success_executor,
    )
    store = FeedbackStore(first.records)

    with pytest.raises(ExperimentRunnerError, match="source ID already exists"):
        run_experiment_pair(
            plan,
            store,
            pair["experiment_id"],
            "repeat benchmark",
            _success_executor,
            task_id=first.task_id,
        )


def test_ready_experiment_requires_explicit_override():
    initial = _plan()
    pair = _model_pair(initial)
    records = []
    store = FeedbackStore()
    plan = initial
    for _ in range(3):
        run_report = run_experiment_pair(
            plan,
            store,
            pair["experiment_id"],
            "fixed benchmark",
            _success_executor,
        )
        records.extend(run_report.records)
        store = FeedbackStore(records)
        plan = _plan(store)

    ready_pair = next(
        item
        for category in plan["categories"]
        for item in category["pairs"]
        if item["experiment_id"] == pair["experiment_id"]
    )
    assert ready_pair["status"] == "ready"
    with pytest.raises(ExperimentRunnerError, match="already ready"):
        run_experiment_pair(
            plan,
            store,
            pair["experiment_id"],
            "unnecessary extra benchmark",
            _success_executor,
        )


def test_stale_saved_plan_stops_after_live_feedback_reaches_threshold():
    stale_plan = _plan()
    pair = _model_pair(stale_plan)
    records = []
    store = FeedbackStore()

    for _ in range(3):
        report = run_experiment_pair(
            stale_plan,
            store,
            pair["experiment_id"],
            "fixed benchmark",
            _success_executor,
        )
        records.extend(report.records)
        store = FeedbackStore(records)

    with pytest.raises(ExperimentRunnerError, match="live evidence"):
        run_experiment_pair(
            stale_plan,
            store,
            pair["experiment_id"],
            "fourth benchmark should be blocked",
            _success_executor,
        )


def test_command_executor_rejects_nonzero_exit_without_model_evidence():
    executor = command_executor(
        ["python", "-c", "import sys; sys.exit(7)"],
        timeout_seconds=10,
    )
    with pytest.raises(RunnerInfrastructureError, match="exited with code 7"):
        executor({"task": "anything"})
