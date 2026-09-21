from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

from .hive_litellm_adapter import (
    HiveAdapterError,
    _DEFAULT_MAX_OUTPUT_TOKENS,
    _DEFAULT_TIMEOUT_SECONDS,
    _RUNNER_SCHEMA_VERSION,
    _SUPPORTED_PROVIDERS,
    _load_hive_transport,
    _positive_float_env,
    _positive_int_env,
    _required_string,
    _verify_wire_configuration,
)

AgentLoopExecutor = Callable[[str, dict[str, str], Any, int, float], dict[str, Any]]


def validate_agent_loop_payload(payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, dict):
        raise HiveAdapterError("runner payload must be a JSON object")
    if payload.get("schema_version") != _RUNNER_SCHEMA_VERSION:
        raise HiveAdapterError(f"runner schema_version must be {_RUNNER_SCHEMA_VERSION}")
    if payload.get("acceptance_mode") != "external_judge":
        raise HiveAdapterError(
            "Hive AgentLoop adapter requires acceptance_mode=external_judge; "
            "a completed loop is not benchmark success"
        )

    task = _required_string(payload, "task")
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise HiveAdapterError("configuration must be a JSON object")
    normalized = {
        key: _required_string(configuration, key)
        for key in ("provider", "model_id", "effort", "execution_mode")
    }
    if normalized["execution_mode"] != "hive_agent_loop":
        raise HiveAdapterError(
            "Hive AgentLoop adapter only supports execution_mode=hive_agent_loop"
        )
    if normalized["provider"] not in _SUPPORTED_PROVIDERS:
        raise HiveAdapterError(
            f"unsupported Hive AgentLoop provider: {normalized['provider']}"
        )
    return task, normalized


def _assistant_text(result: Any) -> str:
    conversation = getattr(result, "conversation", None)
    messages = getattr(conversation, "messages", None)
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if getattr(message, "role", None) != "assistant":
            continue
        content = getattr(message, "content", "")
        if content:
            return str(content)
    return ""


async def _execute_agent_loop_async(
    task: str,
    configuration: dict[str, str],
    provider: Any,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        from framework.agent_loop import AgentContext, AgentLoop, AgentSpec, LoopConfig
    except ImportError as exc:
        raise HiveAdapterError(
            "Hive AgentLoop is not importable; install the repository workspace before using this adapter"
        ) from exc

    spec = AgentSpec(
        id="advisor_benchmark",
        name="AI Model Advisor Benchmark",
        description="Isolated benchmark agent loop",
        input_keys=["task"],
        output_keys=[],
        system_prompt=(
            "Complete the benchmark task directly. Do not ask follow-up questions. "
            "Do not call tools; none are available."
        ),
        tools=[],
        tool_access_policy="none",
    )
    ctx = AgentContext(
        runtime=None,  # AgentLoop does not use DecisionTracker directly.
        agent_id=spec.id,
        agent_spec=spec,
        input_data={"task": task},
        llm=provider,
        available_tools=[],
        max_tokens=max_output_tokens,
        stream_id="judge",
    )
    loop = AgentLoop(
        config=LoopConfig(
            max_iterations=1,
            grace_iterations=0,
            tool_call_budget=0,
            tool_call_lifetime_budget=0,
            max_stream_retries=0,
            capacity_retry_max_seconds=0,
        )
    )
    try:
        result = await asyncio.wait_for(
            loop.execute(ctx),
            timeout=timeout_seconds + 5.0,
        )
    except TimeoutError as exc:
        raise HiveAdapterError("Hive AgentLoop benchmark timed out") from exc

    if not getattr(result, "success", False):
        raise HiveAdapterError(
            "Hive AgentLoop did not complete successfully: "
            f"{str(getattr(result, 'error', '') or 'unknown failure')[:600]}"
        )

    return {
        "response_text": _assistant_text(result),
        "tokens_used": getattr(result, "tokens_used", None),
        "tool_calls_used": getattr(result, "tool_calls_used", None),
        "exit_reason": getattr(result, "exit_reason", None),
        "reliability_stats": getattr(result, "reliability_stats", None),
    }


def _execute_agent_loop(
    task: str,
    configuration: dict[str, str],
    provider: Any,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    return asyncio.run(
        _execute_agent_loop_async(
            task,
            configuration,
            provider,
            max_output_tokens,
            timeout_seconds,
        )
    )


def run_hive_agent_loop_adapter(
    payload: dict[str, Any],
    *,
    transport_loader: Callable[
        [dict[str, str], float], tuple[Any, Callable[[], dict[str, Any] | None], str]
    ]
    | None = None,
    agent_loop_executor: AgentLoopExecutor | None = None,
) -> dict[str, Any]:
    task, configuration = validate_agent_loop_payload(payload)
    timeout_seconds = _positive_float_env(
        "AI_MODEL_ADVISOR_PROVIDER_TIMEOUT_SECONDS",
        _DEFAULT_TIMEOUT_SECONDS,
    )
    max_output_tokens = _positive_int_env(
        "AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS",
        _DEFAULT_MAX_OUTPUT_TOKENS,
    )
    loader = transport_loader or _load_hive_transport
    executor = agent_loop_executor or _execute_agent_loop

    previous_hive_home = os.environ.get("HIVE_HOME")
    with tempfile.TemporaryDirectory(prefix="ai-model-advisor-hive-agent-loop-") as temp_home:
        os.environ["HIVE_HOME"] = temp_home
        try:
            provider, request_reader, litellm_version = loader(
                configuration,
                timeout_seconds,
            )
            started = time.perf_counter()
            loop_result = executor(
                task,
                configuration,
                provider,
                max_output_tokens,
                timeout_seconds,
            )
            latency_seconds = time.perf_counter() - started
            _verify_wire_configuration(configuration, request_reader())
        finally:
            if previous_hive_home is None:
                os.environ.pop("HIVE_HOME", None)
            else:
                os.environ["HIVE_HOME"] = previous_hive_home

    if not isinstance(loop_result, dict):
        raise HiveAdapterError("Hive AgentLoop executor must return a JSON object")

    result: dict[str, Any] = {
        "schema_version": _RUNNER_SCHEMA_VERSION,
        "applied_configuration": configuration,
        "outcome": "partial",
        "response_text": str(loop_result.get("response_text") or ""),
        "latency_seconds": round(latency_seconds, 6),
        "transport": "hive_agent_loop",
        "litellm_version": litellm_version,
        "agent_loop_exit_reason": loop_result.get("exit_reason"),
        "tool_calls_used": loop_result.get("tool_calls_used"),
        "tokens_used": loop_result.get("tokens_used"),
        "reliability_stats": loop_result.get("reliability_stats"),
        "note": (
            "Hive AgentLoop completed and post-transform model/effort wire configuration "
            "was verified; outcome still requires deterministic external judge"
        ),
    }
    return {key: value for key, value in result.items() if value is not None}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = run_hive_agent_loop_adapter(payload)
    except (HiveAdapterError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"hive agent-loop adapter error: {str(exc)[:1200]}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - infrastructure failure is not benchmark evidence
        print(
            f"hive agent-loop adapter infrastructure error: {str(exc)[:1200]}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
