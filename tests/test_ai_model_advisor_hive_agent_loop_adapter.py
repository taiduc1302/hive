from __future__ import annotations

import os

import pytest

from tools.ai_model_advisor.hive_agent_loop_adapter import (
    _validate_agent_loop_evidence,
    run_hive_agent_loop_adapter,
    validate_runner_payload,
)
from tools.ai_model_advisor.hive_litellm_adapter import HiveAdapterError


def _payload(*, effort: str = "high") -> dict:
    return {
        "schema_version": 1,
        "acceptance_mode": "external_judge",
        "task": "Return exactly 42.",
        "configuration": {
            "provider": "openai",
            "model_id": "gpt-6-astra",
            "effort": effort,
            "execution_mode": "hive_agent_loop",
        },
    }


def _evidence() -> dict:
    return {
        "result_success": True,
        "result_exit_reason": "accepted",
        "tool_calls_used": 0,
        "turn_count": 1,
        "turn": {
            "model": "gpt-6-astra",
            "stop_reason": "stop",
            "input_tokens": 20,
            "output_tokens": 3,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
            "cost_usd": 0.01,
        },
        "judge_count": 1,
        "judge": {
            "action": "ACCEPT",
            "judge_type": "implicit",
        },
        "loop_started_count": 1,
        "loop_completed_count": 1,
        "response_text": "42",
    }


def test_agent_loop_payload_requires_agent_loop_mode() -> None:
    payload = _payload()
    payload["configuration"]["execution_mode"] = "single"
    with pytest.raises(HiveAdapterError, match="execution_mode=hive_agent_loop"):
        validate_runner_payload(payload)


def test_agent_loop_evidence_requires_single_turn_implicit_accept() -> None:
    evidence = _evidence()
    _validate_agent_loop_evidence(evidence)

    evidence["turn_count"] = 2
    with pytest.raises(HiveAdapterError, match="exactly one LLM turn"):
        _validate_agent_loop_evidence(evidence)


def test_agent_loop_evidence_rejects_tools() -> None:
    evidence = _evidence()
    evidence["tool_calls_used"] = 1
    with pytest.raises(HiveAdapterError, match="unexpectedly used tools"):
        _validate_agent_loop_evidence(evidence)


def test_agent_loop_adapter_emits_standard_runner_result(monkeypatch) -> None:
    payload = _payload()
    monkeypatch.setenv("HIVE_HOME", "/original/hive-home")

    async def fake_executor(task, configuration, timeout_seconds, max_output_tokens):
        assert task == "Return exactly 42."
        assert configuration == payload["configuration"]
        assert timeout_seconds > 0
        assert max_output_tokens > 0
        request = {
            "body": {
                "model": "gpt-6-astra",
                "reasoning": {"effort": "high"},
            }
        }
        return _evidence(), request, "1.83.4", 0.125

    result = run_hive_agent_loop_adapter(payload, executor=fake_executor)

    assert result["schema_version"] == 1
    assert result["applied_configuration"] == payload["configuration"]
    assert result["outcome"] == "partial"
    assert result["response_text"] == "42"
    assert result["transport"] == "hive_agent_loop"
    assert result["agent_loop_evidence"]["judge_action"] == "ACCEPT"
    assert result["agent_loop_evidence"]["tool_calls_used"] == 0
    assert result["cost_usd"] == 0.01
    assert os.environ["HIVE_HOME"] == "/original/hive-home"


def test_agent_loop_adapter_honors_default_effort_semantics() -> None:
    payload = _payload(effort="default")

    async def fake_executor(task, configuration, timeout_seconds, max_output_tokens):
        return _evidence(), {"body": {"model": "gpt-6-astra"}}, "1.83.4", 0.1

    result = run_hive_agent_loop_adapter(payload, executor=fake_executor)
    assert result["applied_configuration"]["effort"] == "default"
