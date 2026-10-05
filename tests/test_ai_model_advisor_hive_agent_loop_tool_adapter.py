from __future__ import annotations

import json
import os

import pytest

from tools.ai_model_advisor.hive_agent_loop_tool_adapter import (
    _TOOL_NAME,
    _TOOL_RESULT,
    _validate_tool_loop_evidence,
    run_hive_agent_loop_tool_adapter,
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
            "execution_mode": "hive_agent_loop_tool",
        },
    }


def _evidence() -> dict:
    tool_result = json.dumps(_TOOL_RESULT, sort_keys=True)
    return {
        "result_success": True,
        "result_exit_reason": "accepted",
        "tool_calls_used": 1,
        "turns": [
            {
                "model": "gpt-6-astra",
                "stop_reason": "stop",
                "input_tokens": 35,
                "output_tokens": 3,
                "cached_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.02,
            },
            {
                "model": "gpt-6-astra",
                "stop_reason": "tool_calls",
                "input_tokens": 20,
                "output_tokens": 5,
                "cached_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.01,
            },
        ],
        "tool_started": [
            {
                "tool_use_id": "tool-1",
                "tool_name": _TOOL_NAME,
                "tool_input": {},
            }
        ],
        "tool_completed": [
            {
                "tool_use_id": "tool-1",
                "tool_name": _TOOL_NAME,
                "result": tool_result,
                "is_error": False,
            }
        ],
        "judge": [{"action": "ACCEPT", "judge_type": "implicit"}],
        "loop_started_count": 1,
        "loop_completed_count": 1,
        "response_text": "42",
    }


def test_tool_payload_requires_tool_execution_mode() -> None:
    payload = _payload()
    payload["configuration"]["execution_mode"] = "hive_agent_loop"
    with pytest.raises(HiveAdapterError, match="execution_mode=hive_agent_loop_tool"):
        validate_runner_payload(payload)


def test_tool_evidence_requires_exactly_one_tool_and_two_turns() -> None:
    evidence = _evidence()
    _validate_tool_loop_evidence(evidence)

    evidence["tool_calls_used"] = 2
    with pytest.raises(HiveAdapterError, match="exactly one tool call"):
        _validate_tool_loop_evidence(evidence)

    evidence = _evidence()
    evidence["turns"] = evidence["turns"][:1]
    with pytest.raises(HiveAdapterError, match="exactly two LLM turns"):
        _validate_tool_loop_evidence(evidence)


def test_tool_evidence_rejects_wrong_tool_result() -> None:
    evidence = _evidence()
    evidence["tool_completed"][0]["result"] = json.dumps({"ok": True, "nonce": "wrong"})
    with pytest.raises(HiveAdapterError, match="deterministic fixture"):
        _validate_tool_loop_evidence(evidence)


def test_tool_adapter_emits_aggregate_runner_result(monkeypatch) -> None:
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
        return _evidence(), request, "1.83.4", 0.25

    result = run_hive_agent_loop_tool_adapter(payload, executor=fake_executor)

    assert result["schema_version"] == 1
    assert result["applied_configuration"] == payload["configuration"]
    assert result["outcome"] == "partial"
    assert result["response_text"] == "42"
    assert result["transport"] == "hive_agent_loop_tool"
    assert result["agent_loop_evidence"]["turn_count"] == 2
    assert result["agent_loop_evidence"]["tool_calls_used"] == 1
    assert result["agent_loop_evidence"]["tool_name"] == _TOOL_NAME
    assert result["input_tokens"] == 55
    assert result["output_tokens"] == 8
    assert result["cost_usd"] == pytest.approx(0.03)
    assert os.environ["HIVE_HOME"] == "/original/hive-home"


def test_tool_adapter_honors_default_effort_semantics() -> None:
    payload = _payload(effort="default")

    async def fake_executor(task, configuration, timeout_seconds, max_output_tokens):
        return _evidence(), {"body": {"model": "gpt-6-astra"}}, "1.83.4", 0.1

    result = run_hive_agent_loop_tool_adapter(payload, executor=fake_executor)
    assert result["applied_configuration"]["effort"] == "default"
