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


def test_apply_rejects_runner_that_conflicts_with_bound_target_before_launch(
    tmp_path, monkeypatch
) -> None:
    plan_path = tmp_path / "plan.json"
    feedback_path = tmp_path / "feedback.jsonl"
    plan_path.write_text(
        json.dumps(bind_execution_target(_plan(), "provider_api")),
        encoding="utf-8",
    )
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
                "--runner",
                "python",
                "-m",
                "tools.ai_model_advisor.hive_litellm_adapter",
            ]
        )

    assert launched is False
    assert not feedback_path.exists()
