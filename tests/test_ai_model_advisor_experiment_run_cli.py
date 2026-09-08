import json
import sys
from pathlib import Path

import pytest

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.cli import build_parser as build_main_parser
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.experiment_run import ExperimentRunnerError, RunnerInfrastructureError
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
    benchmark = "Implement the fixed benchmark endpoint with PRIVATE_FIXTURE_42."

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
                benchmark,
                "--output",
                str(output),
                "--json-output",
                str(json_output),
            ]
        )
        == 0
    )

    assert not feedback.exists()
    preview_text = json_output.read_text(encoding="utf-8")
    preview = json.loads(preview_text)
    markdown = output.read_text(encoding="utf-8")
    assert preview["mode"] == "preview"
    assert preview["order"] == ["A", "B"]
    assert preview["task_text_included"] is False
    assert "task" not in preview["payloads"]["A"]
    assert "task" not in preview["payloads"]["B"]
    assert benchmark not in preview_text
    assert "PRIVATE_FIXTURE_42" not in markdown
    assert preview["payloads"]["A"]["configuration"]["model_id"] == pair["primary"]["model_id"]
    assert "No executable was launched" in markdown
    assert "intentionally omitted" in markdown


def test_main_cli_alias_runs_preview_without_adapter(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    output = tmp_path / "main-preview.md"
    args = build_main_parser().parse_args(
        [
            "experiment-run",
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
        ]
    )

    assert args.func(args) == 0
    assert not feedback.exists()
    assert "Experiment Run Preview" in output.read_text(encoding="utf-8")


def test_cli_apply_runs_adapter_and_appends_exact_pair(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    output = tmp_path / "run.md"
    json_output = tmp_path / "run.json"
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """import json, sys\npayload = json.load(sys.stdin)\nside = payload['side']\nprint(json.dumps({'schema_version': 1, 'applied_configuration': payload['configuration'], 'outcome': 'success', 'retries': 0, 'cost_usd': 0.1 if side == 'A' else 0.2, 'input_tokens': 100, 'output_tokens': 20}))\n""",
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


def test_cli_builtin_expected_output_judge_sets_objective_outcome(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    expected = tmp_path / "expected.txt"
    expected.write_text("expected answer\n", encoding="utf-8")
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """import json, sys\npayload = json.load(sys.stdin)\nassert payload['acceptance_mode'] == 'external_judge'\nprint(json.dumps({'schema_version': 1, 'applied_configuration': payload['configuration'], 'outcome': 'partial', 'response_text': 'expected answer'}))\n""",
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
                "Return the fixed answer.",
                "--apply",
                "--expected-output-file",
                str(expected),
                "--expected-output-mode",
                "strip-exact",
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )
        == 0
    )

    records = FeedbackStore.load(feedback).records
    assert len(records) == 2
    assert {record.outcome for record in records} == {"success"}
    assert all("judge: trimmed exact text check passed" in record.note for record in records)


def test_invalid_expected_json_is_rejected_before_adapter_launch(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    feedback = tmp_path / "feedback.jsonl"
    expected = tmp_path / "expected.json"
    expected.write_text("not-json", encoding="utf-8")
    launched = tmp_path / "launched.txt"
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        f"from pathlib import Path\nPath({str(launched)!r}).write_text('launched')\n",
        encoding="utf-8",
    )

    with pytest.raises(ExperimentRunnerError, match="JSON fixture is invalid"):
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(feedback),
                "--task",
                "Return JSON.",
                "--apply",
                "--expected-output-file",
                str(expected),
                "--expected-output-mode",
                "json-equal",
                "--runner",
                sys.executable,
                str(adapter),
            ]
        )

    assert not launched.exists()
    assert not feedback.exists()


def test_cli_rejects_external_and_builtin_judges_together(tmp_path):
    plan_path, pair = _write_plan(tmp_path)
    expected = tmp_path / "expected.txt"
    expected.write_text("answer", encoding="utf-8")

    with pytest.raises(ExperimentRunnerError, match="either --judge or --expected-output-file"):
        main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                pair["experiment_id"],
                "--feedback",
                str(tmp_path / "feedback.jsonl"),
                "--task",
                "Return answer.",
                "--expected-output-file",
                str(expected),
                "--judge",
                sys.executable,
                "judge.py",
            ]
        )


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
