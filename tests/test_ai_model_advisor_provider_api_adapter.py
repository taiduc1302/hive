from __future__ import annotations

import pytest

from tools.ai_model_advisor.experiment_run_cli import _with_outcome_judge
from tools.ai_model_advisor.feedback import UsageRecord
from tools.ai_model_advisor.provider_api_adapter import (
    ProviderAdapterError,
    ProviderRequest,
    build_provider_request,
    parse_provider_response,
    validate_runner_payload,
)


def _payload(provider: str, *, execution: str = "single", acceptance: bool = True):
    payload = {
        "schema_version": 1,
        "experiment_id": "implementation-model-abc123",
        "side": "A",
        "category": "implementation",
        "kind": "model",
        "task_id": "implementation-abc123-task-01",
        "task": "Return the fixed benchmark result.",
        "task_sha256": "deadbeef",
        "configuration": {
            "provider": provider,
            "model_id": "gpt-5.6-terra" if provider == "openai" else "claude-sonnet-5",
            "effort": "medium",
            "execution_mode": execution,
        },
    }
    if acceptance:
        payload["acceptance_mode"] = "external_judge"
    return payload


def test_direct_adapter_requires_external_judge():
    with pytest.raises(ProviderAdapterError, match="external_judge"):
        validate_runner_payload(_payload("openai", acceptance=False))


def test_direct_adapter_rejects_orchestration_modes_before_provider_call():
    with pytest.raises(ProviderAdapterError, match="only supports execution_mode=single"):
        validate_runner_payload(_payload("anthropic", execution="ultracode"))


def test_judge_wrapper_marks_adapter_request_for_external_acceptance():
    seen = []

    def executor(payload):
        seen.append(payload)
        return {
            "schema_version": 1,
            "applied_configuration": payload["configuration"],
            "outcome": "partial",
        }

    def judge(_payload):
        return {"schema_version": 1, "outcome": "success"}

    wrapped = _with_outcome_judge(executor, judge)
    result = wrapped(_payload("openai", acceptance=False))

    assert seen[0]["acceptance_mode"] == "external_judge"
    assert result["outcome"] == "success"


def test_openai_request_uses_responses_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-secret")
    monkeypatch.setenv("AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS", "4096")
    request = build_provider_request(_payload("openai"))

    assert request.url == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer test-openai-secret"
    assert "test-openai-secret" not in str(request.body)
    assert request.body == {
        "model": "gpt-5.6-terra",
        "input": "Return the fixed benchmark result.",
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 4096,
        "store": False,
    }


def test_anthropic_request_uses_messages_output_config_effort(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-secret")
    monkeypatch.setenv("AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS", "8192")
    request = build_provider_request(_payload("anthropic"))

    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-anthropic-secret"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "test-anthropic-secret" not in str(request.body)
    assert request.body["model"] == "claude-sonnet-5"
    assert request.body["max_tokens"] == 8192
    assert request.body["messages"] == [
        {"role": "user", "content": "Return the fixed benchmark result."}
    ]
    assert request.body["output_config"] == {"effort": "medium"}


def test_openai_response_parser_preserves_text_and_usage():
    request = ProviderRequest(
        provider="openai",
        url="https://api.openai.com/v1/responses",
        headers={},
        body={},
        configuration=_payload("openai")["configuration"],
    )
    result = parse_provider_response(
        request,
        {
            "id": "resp_1",
            "model": "gpt-5.6-terra",
            "status": "completed",
            "output_text": "candidate answer",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "input_tokens_details": {
                    "cached_tokens": 40,
                    "cache_write_tokens": 10,
                },
            },
        },
    )

    assert result["outcome"] == "partial"
    assert result["response_text"] == "candidate answer"
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 30
    assert result["cached_tokens"] == 40
    assert result["cache_creation_tokens"] == 10
    assert result["applied_configuration"] == request.configuration


def test_anthropic_response_parser_normalizes_total_input_tokens_for_cache_invariant():
    request = ProviderRequest(
        provider="anthropic",
        url="https://api.anthropic.com/v1/messages",
        headers={},
        body={},
        configuration=_payload("anthropic")["configuration"],
    )
    result = parse_provider_response(
        request,
        {
            "id": "msg_1",
            "model": "claude-sonnet-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "candidate answer"}],
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 20,
                "output_tokens": 15,
            },
        },
    )

    assert result["input_tokens"] == 60
    assert result["cached_tokens"] == 30
    assert result["cache_creation_tokens"] == 20
    assert result["output_tokens"] == 15
    UsageRecord(
        provider="anthropic",
        model_id="claude-sonnet-5",
        effort="medium",
        execution_mode="single",
        outcome="success",
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cached_tokens=result["cached_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
    )


def test_provider_response_error_is_infrastructure_failure():
    request = ProviderRequest(
        provider="openai",
        url="https://api.openai.com/v1/responses",
        headers={},
        body={},
        configuration=_payload("openai")["configuration"],
    )
    with pytest.raises(ProviderAdapterError, match="error object"):
        parse_provider_response(request, {"error": {"message": "bad request"}})
