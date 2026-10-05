from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ai_model_advisor.cross_target_experiment import (
    build_agent_loop_overhead_plan,
    cross_target_profiles,
)
from tools.ai_model_advisor.cross_target_run_cli import (
    build_side_dispatch_executor,
    main,
)
from tools.ai_model_advisor.experiment_run import (
    ExperimentRunnerError,
    RunnerInfrastructureError,
)
from tools.ai_model_advisor.feedback import FeedbackStore


def _plan() -> dict:
    return {
        "categories": [
            {
                "category": "debugging",
                "pairs": [
                    {
                        "experiment_id": "exp-overhead",
                        "category": "debugging",
                        "kind": "model",
                        "task_id_template": "overhead-{nn}",
                        "status": "planned",
                        "primary": {
                            "provider": "openai",
                            "model_id": "gpt-6-astra",
                            "effort": "high",
                            "execution_mode": "single",
                        },
                        "challenger": {
                            "provider": "anthropic",
                            "model_id": "claude-opus-5",
                            "effort": "high",
                            "execution_mode": "single",
                        },
                    }
                ],
            }
        ]
    }


def _pair(plan: dict) -> dict:
    return plan["categories"][0]["pairs"][0]


def test_overhead_plan_clones_one_model_and_changes_only_execution() -> None:
    derived = build_agent_loop_overhead_plan(_plan(), "exp-overhead")

    pair = _pair(derived)
    assert pair["kind"] == "execution_target_overhead"
    assert pair["source_side"] == "A"
    for key in ("provider", "model_id", "effort"):
        assert pair["primary"][key] == pair["challenger"][key]
    assert pair["primary"]["execution_mode"] == "hive_agent_loop"
    assert pair["challenger"]["execution_mode"] == "hive_agent_loop_tool"

    profiles = cross_target_profiles(derived)
    assert profiles["A"].host == "hive_agent_loop"
    assert profiles["B"].host == "hive_agent_loop_tool"


def test_overhead_plan_can_use_challenger_as_model_baseline() -> None:
    derived = build_agent_loop_overhead_plan(
        _plan(),
        "exp-overhead",
        source_side="B",
    )
    pair = _pair(derived)
    assert pair["primary"]["provider"] == "anthropic"
    assert pair["challenger"]["model_id"] == "claude-opus-5"


def test_overhead_plan_rejects_same_target() -> None:
    with pytest.raises(ExperimentRunnerError, match="two different hosts"):
        build_agent_loop_overhead_plan(
            _plan(),
            "exp-overhead",
            side_a_host="hive_agent_loop",
            side_b_host="hive_agent_loop",
        )


def test_cross_target_binding_fails_closed_when_tampered() -> None:
    derived = build_agent_loop_overhead_plan(_plan(), "exp-overhead")
    derived["cross_target_execution"]["side_targets"]["B"]["adapter"] = "other"
    with pytest.raises(ExperimentRunnerError, match="does not match current target contract"):
        cross_target_profiles(derived)


def test_dispatcher_uses_side_specific_canonical_runner() -> None:
    derived = build_agent_loop_overhead_plan(_plan(), "exp-overhead")
    commands: list[tuple[str, ...]] = []

    def factory(argv: list[str], timeout_seconds: float):
        commands.append(tuple(argv))
        assert timeout_seconds == 12.0

        def execute(payload: dict) -> dict:
            assert payload["acceptance_mode"] == "external_judge"
            return {
                "schema_version": 1,
                "applied_configuration": payload["configuration"],
                "outcome": "partial",
                "response_text": "42",
            }

        return execute

    executor = build_side_dispatch_executor(
        derived,
        12.0,
        executor_factory=factory,
    )
    pair = _pair(derived)
    for side, key in (("A", "primary"), ("B", "challenger")):
        result = executor(
            {
                "side": side,
                "configuration": pair[key],
            }
        )
        assert result["outcome"] == "partial"

    assert any(
        "tools.ai_model_advisor.hive_agent_loop_adapter" in command
        for command in commands
    )
    assert any(
        "tools.ai_model_advisor.hive_agent_loop_tool_adapter" in command
        for command in commands
    )


def test_dispatcher_rejects_side_mode_mismatch() -> None:
    derived = build_agent_loop_overhead_plan(_plan(), "exp-overhead")

    def factory(argv: list[str], timeout_seconds: float):
        def execute(payload: dict) -> dict:
            raise AssertionError("adapter must not run")

        return execute

    executor = build_side_dispatch_executor(
        derived,
        10.0,
        executor_factory=factory,
    )
    bad = dict(_pair(derived)["primary"])
    bad["execution_mode"] = "hive_agent_loop_tool"
    with pytest.raises(RunnerInfrastructureError, match="does not match target"):
        executor({"side": "A", "configuration": bad})


def test_cross_target_cli_preview_makes_no_adapter_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    feedback = tmp_path / "feedback.jsonl"
    out = tmp_path / "preview.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not construct an execution adapter")

    monkeypatch.setattr(
        "tools.ai_model_advisor.cross_target_run_cli.build_side_dispatch_executor",
        forbidden,
    )
    rc = main(
        [
            "--plan",
            str(plan_path),
            "--experiment-id",
            "exp-overhead",
            "--feedback",
            str(feedback),
            "--task",
            "Return exactly 42.",
            "--json-output",
            str(out),
        ]
    )

    assert rc == 0
    preview = json.loads(out.read_text(encoding="utf-8"))
    assert preview["cross_target_execution"]["side_targets"]["A"]["host"] == (
        "hive_agent_loop"
    )
    assert preview["cross_target_execution"]["side_targets"]["B"]["host"] == (
        "hive_agent_loop_tool"
    )
    assert "task" not in preview["payloads"]["A"]


def test_cross_target_cli_apply_writes_one_atomic_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    feedback_path = tmp_path / "feedback.jsonl"
    expected_path = tmp_path / "expected.txt"
    expected_path.write_text("42", encoding="utf-8")
    result_path = tmp_path / "result.json"

    def fake_dispatcher(plan, timeout_seconds):
        def execute(payload: dict) -> dict:
            return {
                "schema_version": 1,
                "applied_configuration": payload["configuration"],
                "outcome": "partial",
                "response_text": "42",
                "latency_seconds": 0.1 if payload["side"] == "A" else 0.2,
                "cost_usd": 0.01 if payload["side"] == "A" else 0.02,
            }

        return execute

    monkeypatch.setattr(
        "tools.ai_model_advisor.cross_target_run_cli.build_side_dispatch_executor",
        fake_dispatcher,
    )
    rc = main(
        [
            "--plan",
            str(plan_path),
            "--experiment-id",
            "exp-overhead",
            "--feedback",
            str(feedback_path),
            "--task",
            "Return exactly 42.",
            "--expected-output-file",
            str(expected_path),
            "--json-output",
            str(result_path),
            "--apply",
        ]
    )

    assert rc == 0
    store = FeedbackStore.load(feedback_path)
    assert len(store.records) == 2
    assert store.records[0].task_id == store.records[1].task_id
    assert {record.outcome for record in store.records} == {"success"}
    assert {record.execution_mode for record in store.records} == {
        "hive_agent_loop",
        "hive_agent_loop_tool",
    }
    report = json.loads(result_path.read_text(encoding="utf-8"))
    assert report["applied"] is True
    assert report["cross_target_execution"]["side_targets"]["A"]["host"] == (
        "hive_agent_loop"
    )
