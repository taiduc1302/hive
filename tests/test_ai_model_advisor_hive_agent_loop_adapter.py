from __future__ import annotations

import os

import pytest

from tools.ai_model_advisor.hive_agent_loop_adapter import (
    run_hive_agent_loop_adapter,
    validate_agent_loop_payload,
)
from tools.ai_model_advisor.hive_litellm_adapter import HiveAdapterError


def _payload(*, effort: str = "high", mode: str = "hive_agent_loop") -> dict:
    return {
        "schema_version": 1,
        "acceptance_mode": "external_judge",
        "task": "Return exactly 42.",
        "configuration": {
            "provider": "openai",
            "model_id": "gpt-test",
            "effort": effort,
            "execution_mode": mode,
        },
    }


def test_agent_loop_adapter_requires_exact_execution_mode() -> None:
    task, configuration = validate_agent_loop_payload(_payload())
    assert task == "Return exactly 42."
    assert configuration["execution_mode"] == "hive_agent_loop"

    with pytest.raises(HiveAdapterError, match="execution_mode=hive_agent_loop"):
        validate_agent_loop_payload(_payload(mode="single"))


def test_agent_loop_adapter_preserves_external_judge_boundary() -> None:
    payload = _payload()
    payload["acceptance_mode"] = "provider_status"

    with pytest.raises(HiveAdapterError, match="external_judge"):
        validate_agent_loop_payload(payload)


def test_agent_loop_adapter_returns_partial_only_after_wire_proof(monkeypatch) -> None:
    request = {
        "body": {
            "model": "openai/gpt-test",
            "reasoning_effort": "high",
        }
    }

    def loader(configuration, timeout_seconds):
        assert configuration["execution_mode"] == "hive_agent_loop"
        assert timeout_seconds > 0
        return object(), lambda: request, "test-litellm"

    def executor(task, configuration, provider, max_output_tokens, timeout_seconds):
        assert task == "Return exactly 42."
        assert configuration["effort"] == "high"
        assert provider is not None
        assert max_output_tokens > 0
        assert timeout_seconds > 0
        assert os.environ.get("HIVE_HOME")
        return {
            "response_text": "42",
            "tokens_used": 17,
            "tool_calls_used": 0,
            "exit_reason": "completed",
            "reliability_stats": {},
        }

    monkeypatch.delenv("HIVE_HOME", raising=False)
    result = run_hive_agent_loop_adapter(
        _payload(),
        transport_loader=loader,
        agent_loop_executor=executor,
    )

    assert result["outcome"] == "partial"
    assert result["response_text"] == "42"
    assert result["transport"] == "hive_agent_loop"
    assert result["agent_loop_exit_reason"] == "completed"
    assert result["tool_calls_used"] == 0
    assert result["applied_configuration"]["execution_mode"] == "hive_agent_loop"
    assert "HIVE_HOME" not in os.environ


def test_agent_loop_adapter_fails_closed_on_wire_effort_mismatch() -> None:
    def loader(configuration, timeout_seconds):
        return (
            object(),
            lambda: {
                "body": {
                    "model": "openai/gpt-test",
                    "reasoning_effort": "medium",
                }
            },
            "test-litellm",
        )

    def executor(task, configuration, provider, max_output_tokens, timeout_seconds):
        return {"response_text": "42", "exit_reason": "completed"}

    with pytest.raises(HiveAdapterError, match="requested reasoning effort"):
        run_hive_agent_loop_adapter(
            _payload(effort="high"),
            transport_loader=loader,
            agent_loop_executor=executor,
        )


def test_agent_loop_adapter_default_effort_requires_no_explicit_wire_effort() -> None:
    def loader(configuration, timeout_seconds):
        return (
            object(),
            lambda: {"body": {"model": "openai/gpt-test"}},
            "test-litellm",
        )

    def executor(task, configuration, provider, max_output_tokens, timeout_seconds):
        return {"response_text": "42", "exit_reason": "completed"}

    result = run_hive_agent_loop_adapter(
        _payload(effort="default"),
        transport_loader=loader,
        agent_loop_executor=executor,
    )
    assert result["outcome"] == "partial"
