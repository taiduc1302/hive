from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .hive_litellm_adapter import (
    HiveAdapterError,
    _positive_float_env,
    _positive_int_env,
    _provider_api_key,
    _provider_model,
    _verify_wire_configuration,
)

_RUNNER_SCHEMA_VERSION = 1
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_TOKENS = 32768
_TOOL_NAME = "advisor_constant"
_TOOL_RESULT = {"ok": True, "nonce": "ai-model-advisor-tool-v1"}
_SUPPORTED_PROVIDERS = {"openai", "anthropic"}


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HiveAdapterError(f"{key} must be a non-empty string")
    return value.strip()


def validate_runner_payload(payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, dict):
        raise HiveAdapterError("runner payload must be a JSON object")
    if payload.get("schema_version") != _RUNNER_SCHEMA_VERSION:
        raise HiveAdapterError(f"runner schema_version must be {_RUNNER_SCHEMA_VERSION}")
    if payload.get("acceptance_mode") != "external_judge":
        raise HiveAdapterError(
            "Hive AgentLoop tool transport requires acceptance_mode=external_judge; "
            "tool/lifecycle completion is not benchmark success"
        )

    task = _required_string(payload, "task")
    raw = payload.get("configuration")
    if not isinstance(raw, dict):
        raise HiveAdapterError("configuration must be a JSON object")
    configuration = {
        key: _required_string(raw, key)
        for key in ("provider", "model_id", "effort", "execution_mode")
    }
    if configuration["execution_mode"] != "hive_agent_loop_tool":
        raise HiveAdapterError(
            "Hive AgentLoop tool adapter only supports "
            "execution_mode=hive_agent_loop_tool"
        )
    if configuration["provider"] not in _SUPPORTED_PROVIDERS:
        raise HiveAdapterError(
            f"unsupported Hive AgentLoop tool provider: {configuration['provider']}"
        )
    return task, configuration


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_data(event: Any) -> dict[str, Any]:
    data = getattr(event, "data", None)
    return dict(data) if isinstance(data, dict) else {}


def _collect_tool_loop_evidence(
    event_bus: Any,
    event_type: Any,
    execution_id: str,
    result: Any,
) -> dict[str, Any]:
    turns = event_bus.get_history(
        event_type.LLM_TURN_COMPLETE,
        execution_id=execution_id,
        limit=10,
    )
    tool_started = event_bus.get_history(
        event_type.TOOL_CALL_STARTED,
        execution_id=execution_id,
        limit=10,
    )
    tool_completed = event_bus.get_history(
        event_type.TOOL_CALL_COMPLETED,
        execution_id=execution_id,
        limit=10,
    )
    verdicts = event_bus.get_history(
        event_type.JUDGE_VERDICT,
        execution_id=execution_id,
        limit=10,
    )
    started = event_bus.get_history(
        event_type.NODE_LOOP_STARTED,
        execution_id=execution_id,
        limit=10,
    )
    completed = event_bus.get_history(
        event_type.NODE_LOOP_COMPLETED,
        execution_id=execution_id,
        limit=10,
    )
    deltas = event_bus.get_history(
        event_type.LLM_TEXT_DELTA,
        execution_id=execution_id,
        limit=1000,
    )
    latest_snapshot = ""
    if deltas:
        latest_snapshot = str(_event_data(deltas[0]).get("snapshot") or "")

    return {
        "result_success": bool(getattr(result, "success", False)),
        "result_exit_reason": str(getattr(result, "exit_reason", "") or ""),
        "tool_calls_used": int(getattr(result, "tool_calls_used", 0) or 0),
        "turns": [_event_data(event) for event in turns],
        "tool_started": [_event_data(event) for event in tool_started],
        "tool_completed": [_event_data(event) for event in tool_completed],
        "judge": [_event_data(event) for event in verdicts],
        "loop_started_count": len(started),
        "loop_completed_count": len(completed),
        "response_text": latest_snapshot,
    }


def _validate_tool_loop_evidence(evidence: dict[str, Any]) -> None:
    if not evidence.get("result_success"):
        raise HiveAdapterError(
            "Hive AgentLoop tool benchmark did not complete successfully"
        )
    if evidence.get("tool_calls_used") != 1:
        raise HiveAdapterError(
            "controlled Hive AgentLoop tool benchmark must dispatch exactly one tool call"
        )

    turns = evidence.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise HiveAdapterError(
            "controlled Hive AgentLoop tool benchmark must prove exactly two LLM turns"
        )

    started = evidence.get("tool_started")
    completed = evidence.get("tool_completed")
    if not isinstance(started, list) or len(started) != 1:
        raise HiveAdapterError("benchmark must prove exactly one tool-start event")
    if not isinstance(completed, list) or len(completed) != 1:
        raise HiveAdapterError("benchmark must prove exactly one tool-complete event")

    start = started[0]
    finish = completed[0]
    if start.get("tool_name") != _TOOL_NAME or finish.get("tool_name") != _TOOL_NAME:
        raise HiveAdapterError("benchmark executed an unexpected tool")
    if start.get("tool_input") not in ({}, None):
        raise HiveAdapterError("benchmark tool must be called with no arguments")
    if start.get("tool_use_id") != finish.get("tool_use_id"):
        raise HiveAdapterError("tool lifecycle IDs do not match")
    if finish.get("is_error"):
        raise HiveAdapterError("benchmark tool returned an error")
    if finish.get("result") != json.dumps(_TOOL_RESULT, sort_keys=True):
        raise HiveAdapterError("benchmark tool result does not match deterministic fixture")

    if evidence.get("loop_started_count") != 1 or evidence.get("loop_completed_count") != 1:
        raise HiveAdapterError("Hive AgentLoop lifecycle events are incomplete")

    verdicts = evidence.get("judge")
    if not isinstance(verdicts, list) or len(verdicts) != 1:
        raise HiveAdapterError("benchmark must prove exactly one judge verdict")
    verdict = verdicts[0]
    if verdict.get("action") != "ACCEPT" or verdict.get("judge_type") != "implicit":
        raise HiveAdapterError(
            "Hive AgentLoop tool benchmark did not finish through implicit ACCEPT"
        )
    if any(not isinstance(turn, dict) or not turn.get("model") for turn in turns):
        raise HiveAdapterError("Hive AgentLoop turn metadata is incomplete")


async def _execute_hive_agent_loop_tool(
    task: str,
    configuration: dict[str, str],
    timeout_seconds: float,
    max_output_tokens: int,
    *,
    provider_factory: Callable[
        [dict[str, str], float],
        tuple[Any, Callable[[], dict[str, Any] | None], str],
    ]
    | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str, float]:
    core_dir = _repo_root() / "core"
    core_text = str(core_dir)
    if core_text not in sys.path:
        sys.path.insert(0, core_text)

    try:
        import litellm as litellm_package
        from framework.agent_loop.agent_loop import AgentLoop
        from framework.agent_loop.internals.types import LoopConfig
        from framework.agent_loop.types import AgentContext, AgentSpec
        from framework.host.event_bus import EventBus, EventType
        from framework.llm import litellm as hive_litellm
        from framework.llm.litellm import LiteLLMProvider
        from framework.llm.provider import Tool, ToolResult
    except ImportError as exc:
        raise HiveAdapterError(
            "Hive AgentLoop runtime is not importable; install the repository core workspace"
        ) from exc

    if provider_factory is None:
        provider_kwargs: dict[str, Any] = {
            "model": _provider_model(configuration),
            "api_key": _provider_api_key(configuration),
            "timeout": timeout_seconds,
        }
        if configuration["effort"] != "default":
            provider_kwargs["reasoning_effort"] = configuration["effort"]
        provider = LiteLLMProvider(**provider_kwargs)
        request_reader = hive_litellm._last_llm_request.get
        litellm_version = str(getattr(litellm_package, "__version__", "unknown"))
    else:
        provider, request_reader, litellm_version = provider_factory(
            configuration,
            timeout_seconds,
        )

    tool = Tool(
        name=_TOOL_NAME,
        description=(
            "Deterministic benchmark tool. Call this tool exactly once before "
            "answering the user's task. It takes no arguments."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        concurrency_safe=True,
    )

    def tool_executor(tool_use: Any) -> Any:
        if getattr(tool_use, "name", None) != _TOOL_NAME:
            return ToolResult(
                tool_use_id=str(getattr(tool_use, "id", "")),
                content="unexpected benchmark tool",
                is_error=True,
            )
        tool_input = getattr(tool_use, "input", None) or {}
        if tool_input:
            return ToolResult(
                tool_use_id=str(getattr(tool_use, "id", "")),
                content="benchmark tool takes no arguments",
                is_error=True,
            )
        return ToolResult(
            tool_use_id=str(getattr(tool_use, "id", "")),
            content=json.dumps(_TOOL_RESULT, sort_keys=True),
        )

    execution_id = f"advisor-agent-loop-tool-{uuid.uuid4().hex[:16]}"
    bus = EventBus(max_history=500)
    spec = AgentSpec(
        id="advisor_tool_benchmark",
        name="AI Model Advisor AgentLoop Tool Benchmark",
        description="Controlled one-tool benchmark path for Hive AgentLoop.",
        system_prompt=(
            "Before answering the user's task, call advisor_constant exactly once. "
            "After its result arrives, answer the user's task directly and concisely. "
            "Do not call any other tool."
        ),
        output_keys=[],
        tools=[_TOOL_NAME],
        tool_access_policy="explicit",
        skip_judge=False,
    )
    ctx = AgentContext(
        runtime=None,
        agent_id=spec.id,
        agent_spec=spec,
        input_data={"task": task},
        llm=provider,
        available_tools=[tool],
        max_tokens=max_output_tokens,
        stream_id="judge",
        execution_id=execution_id,
    )
    loop = AgentLoop(
        event_bus=bus,
        config=LoopConfig(
            max_iterations=1,
            grace_iterations=0,
            max_stream_retries=0,
            capacity_retry_max_seconds=0.0,
            tool_call_budget=1,
            tool_call_lifetime_budget=0,
        ),
        tool_executor=tool_executor,
    )

    started_at = time.perf_counter()
    result = await asyncio.wait_for(loop.execute(ctx), timeout=timeout_seconds)
    latency_seconds = time.perf_counter() - started_at
    evidence = _collect_tool_loop_evidence(bus, EventType, execution_id, result)
    return evidence, request_reader(), litellm_version, latency_seconds


def _build_result(
    configuration: dict[str, str],
    evidence: dict[str, Any],
    *,
    litellm_version: str,
    latency_seconds: float,
) -> dict[str, Any]:
    turns = evidence["turns"]
    last_turn = turns[0]
    total_input = sum(int(turn.get("input_tokens") or 0) for turn in turns)
    total_output = sum(int(turn.get("output_tokens") or 0) for turn in turns)
    total_cached = sum(int(turn.get("cached_tokens") or 0) for turn in turns)
    total_cache_creation = sum(
        int(turn.get("cache_creation_tokens") or 0) for turn in turns
    )
    total_cost = sum(float(turn.get("cost_usd") or 0.0) for turn in turns)

    result: dict[str, Any] = {
        "schema_version": _RUNNER_SCHEMA_VERSION,
        "applied_configuration": configuration,
        "outcome": "partial",
        "response_text": str(evidence.get("response_text") or ""),
        "resolved_model": last_turn.get("model"),
        "provider_status": last_turn.get("stop_reason"),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cached_tokens": total_cached,
        "cache_creation_tokens": total_cache_creation,
        "latency_seconds": round(latency_seconds, 6),
        "transport": "hive_agent_loop_tool",
        "litellm_version": litellm_version,
        "agent_loop_evidence": {
            "turn_count": len(turns),
            "tool_calls_used": evidence["tool_calls_used"],
            "tool_name": _TOOL_NAME,
            "tool_start_count": len(evidence["tool_started"]),
            "tool_complete_count": len(evidence["tool_completed"]),
            "judge_count": len(evidence["judge"]),
            "judge_action": evidence["judge"][0].get("action"),
            "judge_type": evidence["judge"][0].get("judge_type"),
            "loop_started_count": evidence["loop_started_count"],
            "loop_completed_count": evidence["loop_completed_count"],
            "exit_reason": evidence.get("result_exit_reason"),
        },
        "note": (
            "Hive AgentLoop completed a deterministic one-tool/two-turn lifecycle "
            "and wire configuration was verified; benchmark outcome still requires "
            "the external deterministic judge"
        ),
    }
    if total_cost > 0:
        result["cost_usd"] = total_cost
    credits = [
        float(turn["credits"])
        for turn in turns
        if isinstance(turn.get("credits"), (int, float))
    ]
    if credits:
        result["credits"] = sum(credits)
    return result


def run_hive_agent_loop_tool_adapter(
    payload: dict[str, Any],
    *,
    executor: Callable[
        [str, dict[str, str], float, int],
        Awaitable[tuple[dict[str, Any], dict[str, Any] | None, str, float]],
    ]
    | None = None,
) -> dict[str, Any]:
    task, configuration = validate_runner_payload(payload)
    timeout_seconds = _positive_float_env(
        "AI_MODEL_ADVISOR_PROVIDER_TIMEOUT_SECONDS",
        _DEFAULT_TIMEOUT_SECONDS,
    )
    max_output_tokens = _positive_int_env(
        "AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS",
        _DEFAULT_MAX_OUTPUT_TOKENS,
    )
    execute = executor or _execute_hive_agent_loop_tool

    previous_hive_home = os.environ.get("HIVE_HOME")
    with tempfile.TemporaryDirectory(prefix="ai-model-advisor-agent-loop-tool-") as temp_home:
        os.environ["HIVE_HOME"] = temp_home
        try:
            evidence, wire_request, litellm_version, latency_seconds = asyncio.run(
                execute(task, configuration, timeout_seconds, max_output_tokens)
            )
            _validate_tool_loop_evidence(evidence)
            _verify_wire_configuration(configuration, wire_request)
        finally:
            if previous_hive_home is None:
                os.environ.pop("HIVE_HOME", None)
            else:
                os.environ["HIVE_HOME"] = previous_hive_home

    return _build_result(
        configuration,
        evidence,
        litellm_version=litellm_version,
        latency_seconds=latency_seconds,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = run_hive_agent_loop_tool_adapter(payload)
    except (HiveAdapterError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"hive agent-loop tool adapter error: {str(exc)[:1200]}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            f"hive agent-loop tool infrastructure error: {str(exc)[:1200]}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
