from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.llm.provider import LLMProvider, LLMResponse
from framework.llm.stream_events import FinishEvent, TextDeltaEvent
from tools.ai_model_advisor.hive_agent_loop_adapter import _execute_hive_agent_loop


class FakeStreamingProvider(LLMProvider):
    model = "openai/gpt-6-astra"

    def complete(self, messages, system="", tools=None, max_tokens=None, **kwargs):
        return LLMResponse(
            content="42",
            model="gpt-6-astra",
            input_tokens=20,
            output_tokens=3,
            stop_reason="stop",
        )

    async def stream(
        self,
        messages,
        system="",
        tools=None,
        max_tokens=None,
        system_dynamic_suffix=None,
    ):
        yield TextDeltaEvent(content="42", snapshot="42")
        yield FinishEvent(
            stop_reason="stop",
            input_tokens=20,
            output_tokens=3,
            model="gpt-6-astra",
        )


@pytest.mark.asyncio
async def test_real_agent_loop_bridge_produces_lifecycle_evidence() -> None:
    request = {
        "body": {
            "model": "gpt-6-astra",
            "reasoning": {"effort": "high"},
        }
    }

    def provider_factory(configuration, timeout_seconds):
        assert configuration["execution_mode"] == "hive_agent_loop"
        assert timeout_seconds > 0
        return FakeStreamingProvider(), lambda: request, "test"

    evidence, captured_request, version, latency = await _execute_hive_agent_loop(
        "Return exactly 42.",
        {
            "provider": "openai",
            "model_id": "gpt-6-astra",
            "effort": "high",
            "execution_mode": "hive_agent_loop",
        },
        5.0,
        128,
        provider_factory=provider_factory,
    )

    assert evidence["result_success"] is True
    assert evidence["turn_count"] == 1
    assert evidence["judge_count"] == 1
    assert evidence["judge"]["action"] == "ACCEPT"
    assert evidence["judge"]["judge_type"] == "implicit"
    assert evidence["loop_started_count"] == 1
    assert evidence["loop_completed_count"] == 1
    assert evidence["tool_calls_used"] == 0
    assert evidence["response_text"] == "42"
    assert captured_request == request
    assert version == "test"
    assert latency >= 0
