import json
import sys
from pathlib import Path

import pytest

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.experiment_judge import (
    apply_outcome_judge,
    command_judge,
    judge_payload,
)
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.experiment_run import RunnerInfrastructureError
from tools.ai_model_advisor.experiment_run_cli import main
from tools.ai_model_advisor.feedback import FeedbackStore
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _write_plan(tmp_path):
    profiles = ActivityAnalyzer().category_profiles_from_texts(
        ["Implement a backend API endpoint and update tests"]
    )
    plan = build_experiment_plan(
        profiles,
        RecommendationEngine(ModelRegistry(REGISTRY), FeedbackStore()),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    category = next(item for item in plan["categories"] if item["category"] == "implementation")
    pair = next(item for item in category["pairs"] if item["kind"] == "model")
    return plan_path, pair


def test_judge_payload_preserves_task_config_and_adapter_result():
    runner = {
        "experiment_id": "exp-1",
        "side": "A",
        "category": "implementation",
        "kind": "model",
        "task_id": "task-01",
        "task": "fixed benchmark",
        "task_sha256": "abc",
        "configuration": {
            "provider": "openai",
            "model_id": "gpt-5.6-terra",
            "effort": "medium",
            "execution_mode": "single",
        },
    }
    adapter = {"response_text": "candidate output", "latency_seconds": 4.2}
    payload = judge_payload(runner, adapter)

    assert payload["schema_version"] == 1
    assert payload["task"] == "fixed benchmark"
    assert payload["configuration"] == runner["configuration"]
    assert payload["adapter_result"] == adapter


def test_judge_overrides_adapter_self_reported_outcome_without_changing_metrics():
    runner = {
        "experiment_id": "exp-1",
        "side": "A",
        "category": "implementation",
        "kind": "model",
        "task_id": "task-01",
        "task": "fixed benchmark",
        "task_sha256": "abc",
        "configuration": {},
    }
    adapter = {
        "outcome": "success",
        "latency_seconds": 12.5,
        "cost_usd": 0.42,
        "note": "adapter completed",
    }

    result = apply_outcome_judge(
        runner,
        adapter,
        lambda _payload: {
            "schema_version": 1,
            "outcome": "failure",
            "note": "acceptance tests failed",
        },
    )

    assert result["outcome"] == "failure"
    assert result["outcome_source"] == "judge"
    assert result["latency_seconds"] == 12.5
    assert result["cost_usd"] == 0.42
    assert "adapter completed" in result["note"]
    assert "judge: acceptance tests failed" in result["note"]


def test_cli_judge_can_turn_adapter_success_into_model_failure(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback_path = tmp_path / "feedback.jsonl"
    adapter = tmp_path / "adapter.py"
    judge = tmp_path / "judge.py"
    adapter.write_text(
        """import json, sys\np = json.load(sys.stdin)\nprint(json.dumps({'schema_version': 1, 'applied_configuration': p['configuration'], 'outcome': 'success', 'latency_seconds': 2.0, 'response_text': 'wrong answer'}))\n""",
        encoding="utf-8",
    )
    judge.write_text(
        """import json, sys\np = json.load(sys.stdin)\nassert p['adapter_result']['response_text'] == 'wrong answer'\nprint(json.dumps({'schema_version': 1, 'outcome': 'failure', 'note': 'deterministic check failed'}))\n""",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(feedback_path),
                "--task",
                "fixed benchmark",
                "--apply",
                "--judge",
                sys.executable,
                str(judge),
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )
        == 0
    )

    records = FeedbackStore.load(feedback_path).records
    assert len(records) == 2
    assert {record.outcome for record in records} == {"failure"}
    assert all(record.latency_seconds == 2.0 for record in records)
    assert all("judge: deterministic check failed" in record.note for record in records)


def test_cli_judge_failure_creates_no_feedback(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback_path = tmp_path / "feedback.jsonl"
    adapter = tmp_path / "adapter.py"
    judge = tmp_path / "judge.py"
    adapter.write_text(
        """import json, sys\np = json.load(sys.stdin)\nprint(json.dumps({'schema_version': 1, 'applied_configuration': p['configuration'], 'outcome': 'success'}))\n""",
        encoding="utf-8",
    )
    judge.write_text("import sys\nsys.exit(8)\n", encoding="utf-8")

    with pytest.raises(RunnerInfrastructureError, match="judge exited with code 8"):
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(feedback_path),
                "--task",
                "fixed benchmark",
                "--apply",
                "--judge",
                sys.executable,
                str(judge),
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )

    assert not feedback_path.exists()


def test_command_judge_rejects_nonzero_exit():
    judge = command_judge(
        [sys.executable, "-c", "import sys; sys.exit(5)"],
        timeout_seconds=10,
    )
    with pytest.raises(RunnerInfrastructureError, match="judge exited with code 5"):
        judge({"task": "anything"})
