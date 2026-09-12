from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.ai_model_advisor.hive_litellm_adapter import (
    HiveAdapterError,
    _verify_wire_configuration,
    run_hive_adapter,
    validate_runner_payload,
)


def _payload(provider: str = "openai", effort: str = "high") -> dict:
    return {
        "schema_version": 1,
        "acceptance_mode": "external_judge",
        "task": "Return exactly 42.",
        "configuration": {
            "provider": provider,
            "model_id": "gpt-6-astra" if provider == "openai" else "claude-opus-5",
            "effort": effort,
            "execution_mode": "single",
        },
    }


def test_hive_adapter_rejects_orchestration_modes() -> None:
    payload = _payload()
    payload["configuration"]["execution_mode"] = "subagents"
    with pytest.raises(HiveAdapterError, match="only supports execution_mode=single"):
        validate_runner_payload(payload)


def test_wire_verification_accepts_openai_reasoning_effort_shapes() -> None:
    config = _payload()["configuration"]
    _verify_wire_configuration(
        config,
        {"body": {"model": "gpt-6-astra", "reasoning_effort": "high"}},
    )
    _verify_wire_configuration(
        config,
        {"body": {"model": "gpt-6-astra", "reasoning": {"effort": "high"}}},
    )


def test_wire_verification_requires_anthropic_output_config() -> None:
    config = _payload(provider="anthropic", effort="xhigh")["configuration"]
    _verify_wire_configuration(
        config,
        {"body": {"model": "claude-opus-5", "output_config": {"effort": "xhigh"}}},
    )
    with pytest.raises(HiveAdapterError, match="did not prove"):
        _verify_wire_configuration(
            config,
            {"body": {"model": "claude-opus-5", "reasoning_effort": "xhigh"}},
        )


def test_wire_verification_rejects_model_mismatch() -> None:
    config = _payload()["configuration"]
    with pytest.raises(HiveAdapterError, match="wire model mismatch"):
        _verify_wire_configuration(
            config,
            {"body": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}},
        )


def test_hive_adapter_returns_runner_schema_and_restores_hive_home(monkeypatch) -> None:
    payload = _payload()
    monkeypatch.setenv("HIVE_HOME", "/original/hive-home")

    class FakeProvider:
        def complete(self, **kwargs):
            assert kwargs["messages"][0]["content"] == "Return exactly 42."
            assert kwargs["max_retries"] == 0
            return SimpleNamespace(
                content="42",
                model="gpt-6-astra",
                input_tokens=12,
                output_tokens=3,
                cached_tokens=2,
                cache_creation_tokens=0,
                cost_usd=0.004,
                credits=None,
                stop_reason="stop",
            )

    def fake_loader(configuration, timeout_seconds):
        assert configuration["effort"] == "high"
        assert timeout_seconds > 0
        captured = {
            "body": {
                "model": "gpt-6-astra",
                "reasoning": {"effort": "high"},
            }
        }
        return FakeProvider(), lambda: captured, "1.83.4"

    result = run_hive_adapter(payload, transport_loader=fake_loader)

    assert result["schema_version"] == 1
    assert result["applied_configuration"] == payload["configuration"]
    assert result["outcome"] == "partial"
    assert result["response_text"] == "42"
    assert result["transport"] == "hive_litellm"
    assert result["litellm_version"] == "1.83.4"
    assert result["cost_usd"] == 0.004
    assert result["cached_tokens"] == 2
    assert result["latency_seconds"] >= 0
    assert __import__("os").environ["HIVE_HOME"] == "/original/hive-home"


def test_hive_adapter_fails_closed_when_effort_disappears() -> None:
    payload = _payload()

    class FakeProvider:
        def complete(self, **kwargs):
            return SimpleNamespace(
                content="42",
                model="gpt-6-astra",
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                cache_creation_tokens=0,
                cost_usd=0.0,
                credits=None,
                stop_reason="stop",
            )

    def fake_loader(configuration, timeout_seconds):
        captured = {"body": {"model": configuration["model_id"]}}
        return FakeProvider(), lambda: captured, "1.83.4"

    with pytest.raises(HiveAdapterError, match="did not prove"):
        run_hive_adapter(payload, transport_loader=fake_loader)
