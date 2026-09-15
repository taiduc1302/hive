from __future__ import annotations

import json

import pytest

from tools.ai_model_advisor import experiment_run_cli
from tools.ai_model_advisor.experiment_run import ExperimentRunnerError
from tools.ai_model_advisor.experiment_target import bind_execution_target


def _plan() -> dict:
    return {
        "categories": [
            {
                "category": "implementation",
                "pairs": [
                    {
                        "experiment_id": "target-cli-01",
                        "category": "implementation",
                        "kind": "model",
                        "status": "planned",
                        "task_id_template": "implementation-target-cli-01-task-{nn}",
                        "primary": {
                            "provider": "openai",
                            "model_id": "gpt-5.6-terra",
                            "effort": "medium",
                            "execution_mode": "single",
                        },
                        "challenger": {
                            "provider": "anthropic",
                            "model_id": "claude-haiku-4-5-20251001",
                            "effort": "default",
                            "execution_mode": "single",
                        },
                    }
                ],
            }
        ]
    }


def _write_plan(tmp_path, plan: dict):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


def _expected_fixture(tmp_path):
    path = tmp_path / "expected.txt"
    path.write_text("fixed answer", encoding="utf-8")
    return path


def test_apply_rejects_runner_that_conflicts_with_bound_target_before_launch(
    tmp_path, monkeypatch
) -> None:
    plan_path = _write_plan(tmp_path, bind_execution_target(_plan(), "provider_api"))
    feedback_path = tmp_path / "feedback.jsonl"
    expected_path = _expected_fixture(tmp_path)
    launched = False

    def forbidden_executor(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("command_executor must not be reached for a target mismatch")

    monkeypatch.setattr(experiment_run_cli, "command_executor", forbidden_executor)

    with pytest.raises(ExperimentRunnerError, match="Bound experiment target requires runner module"):
        experiment_run_cli.main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                "target-cli-01",
                "--feedback",
                str(feedback_path),
                "--task",
                "Return the fixed answer.",
                "--apply",
                "--expected-output-file",
                str(expected_path),
                "--runner",
                "python",
                "-m",
                "tools.ai_model_advisor.hive_litellm_adapter",
            ]
        )

    assert launched is False
    assert not feedback_path.exists()


def test_bound_target_requires_judge_before_runner_launch(tmp_path, monkeypatch) -> None:
    plan_path = _write_plan(tmp_path, bind_execution_target(_plan(), "provider_api"))
    feedback_path = tmp_path / "feedback.jsonl"
    launched = False

    def forbidden_executor(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("command_executor must not run without required judge")

    monkeypatch.setattr(experiment_run_cli, "command_executor", forbidden_executor)

    with pytest.raises(ExperimentRunnerError, match="requires a deterministic outcome judge"):
        experiment_run_cli.main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                "target-cli-01",
                "--feedback",
                str(feedback_path),
                "--task",
                "Return the fixed answer.",
                "--apply",
                "--use-target",
            ]
        )

    assert launched is False
    assert not feedback_path.exists()


def test_use_target_resolves_bound_provider_adapter_without_manual_runner(
    tmp_path, monkeypatch
) -> None:
    plan_path = _write_plan(tmp_path, bind_execution_target(_plan(), "provider_api"))
    feedback_path = tmp_path / "feedback.jsonl"
    expected_path = _expected_fixture(tmp_path)
    captured_argv: list[str] = []

    def fake_command_executor(argv, _timeout_seconds):
        captured_argv.extend(argv)

        def execute(payload):
            return {
                "schema_version": 1,
                "applied_configuration": payload["configuration"],
                "outcome": "partial",
                "response_text": "fixed answer",
            }

        return execute

    monkeypatch.setattr(experiment_run_cli, "command_executor", fake_command_executor)

    assert (
        experiment_run_cli.main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                "target-cli-01",
                "--feedback",
                str(feedback_path),
                "--task",
                "Return the fixed answer.",
                "--apply",
                "--use-target",
                "--expected-output-file",
                str(expected_path),
            ]
        )
        == 0
    )

    assert captured_argv[1:] == [
        "-m",
        "tools.ai_model_advisor.provider_api_adapter",
    ]
    records = [json.loads(line) for line in feedback_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert {record["outcome"] for record in records} == {"success"}


def test_use_target_requires_bound_plan_before_launch(tmp_path, monkeypatch) -> None:
    plan_path = _write_plan(tmp_path, _plan())
    feedback_path = tmp_path / "feedback.jsonl"
    launched = False

    def forbidden_executor(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("command_executor must not run for an unbound --use-target plan")

    monkeypatch.setattr(experiment_run_cli, "command_executor", forbidden_executor)

    with pytest.raises(ExperimentRunnerError, match="bind an execution target first"):
        experiment_run_cli.main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                "target-cli-01",
                "--feedback",
                str(feedback_path),
                "--task",
                "Return the fixed answer.",
                "--apply",
                "--use-target",
            ]
        )

    assert launched is False
    assert not feedback_path.exists()


def test_use_target_and_runner_are_mutually_exclusive(tmp_path) -> None:
    plan_path = _write_plan(tmp_path, bind_execution_target(_plan(), "provider_api"))

    with pytest.raises(ExperimentRunnerError, match="either --use-target or --runner"):
        experiment_run_cli.main(
            [
                "--plan",
                str(plan_path),
                "--experiment-id",
                "target-cli-01",
                "--feedback",
                str(tmp_path / "feedback.jsonl"),
                "--task",
                "Return the fixed answer.",
                "--apply",
                "--use-target",
                "--runner",
                "python",
                "-m",
                "tools.ai_model_advisor.provider_api_adapter",
            ]
        )
