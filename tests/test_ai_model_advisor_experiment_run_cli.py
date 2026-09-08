import json
import sys
from pathlib import Path

import pytest

from tools.ai_model_advisor.activity import ActivityAnalyzer
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


def test_cli_preview_does_not_require_or_launch_runner(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    output = tmp_path / "preview.md"
    json_output = tmp_path / "preview.json"

    assert (
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(feedback),
                "--task",
                "Implement the fixed benchmark endpoint.",
                "--output",
                str(output),
                "--json-output",
                str(json_output),
            ]
        )
        == 0
    )

    assert not feedback.exists()
    preview = json.loads(json_output.read_text(encoding="utf-8"))
    assert preview["mode"] == "preview"
    assert preview["order"] == ["A", "B"]
    assert preview["payloads"]["A"]["configuration"]["model_id"] == pair["primary"]["model_id"]
    assert "No executable was launched" in output.read_text(encoding="utf-8")


def test_cli_apply_runs_adapter_and_appends_exact_pair(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    output = tmp_path / "run.md"
    json_output = tmp_path / "run.json"
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """import json, sys\npayload = json.load(sys.stdin)\nside = payload['side']\nprint(json.dumps({'outcome': 'success', 'retries': 0, 'cost_usd': 0.1 if side == 'A' else 0.2, 'input_tokens': 100, 'output_tokens': 20}))\n""",
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
                str(feedback),
                "--task",
                "Implement the fixed benchmark endpoint.",
                "--apply",
                "--output",
                str(output),
                "--json-output",
                str(json_output),
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )
        == 0
    )

    store = FeedbackStore.load(feedback)
    assert len(store.records) == 2
    assert {record.model_id for record in store.records} == {
        pair["primary"]["model_id"],
        pair["challenger"]["model_id"],
    }
    assert len({record.task_id for record in store.records}) == 1
    result = json.loads(json_output.read_text(encoding="utf-8"))
    assert result["applied"] is True
    assert result["order"] == ["A", "B"]
    assert "Feedback written: **yes**" in output.read_text(encoding="utf-8")


def test_cli_infrastructure_failure_leaves_feedback_unchanged(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    adapter = tmp_path / "broken_adapter.py"
    adapter.write_text("import sys\nsys.exit(9)\n", encoding="utf-8")

    with pytest.raises(RunnerInfrastructureError, match="exited with code 9"):
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(feedback),
                "--task",
                "Implement the fixed benchmark endpoint.",
                "--apply",
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )

    assert not feedback.exists()
