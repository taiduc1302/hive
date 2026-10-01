from __future__ import annotations

import argparse
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
            "Hive AgentLoop transport requires acceptance_mode=external_judge; "
            "implicit AgentLoop ACCEPT is lifecycle evidence, not benchmark success"
        )

    task = _required_string(payload, "task")
    raw = payload.get("configuration")
    if not isinstance(raw, dict):
        raise HiveAdapterError("configuration must be a JSON object")
    configuration = {
        key: _required_string(raw, key)
        for key in ("provider", "model_id", "effort", "execution_mode")
    }
    if configuration["execution_mode"] != "hive_agent_loop":
        raise HiveAdapterError(
            "Hive AgentLoop adapter only supports execution_mode=hive_agent_loop"
        )
    if configuration["provider"] not in _SUPPORTED_PROVIDERS:
        raise HiveAdapterError(
            f"unsupported Hive AgentLoop provider: {configuration['provider']}"
        )
    return task, configuration


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _event_data(event: Any) -> dict[str, Any]:
    data = getattr(event, "data", None)
    return dict(data) if isinstance(data, dict) else {}


def _collect_agent_loop_evidence(
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
    deltas = event_bus.get_history(
        event_type.LLM_TEXT_DELTA,
        execution_id=execution_id,
        limit=1000,
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
    latest_snapshot = ""
    if deltas:
        latest_snapshot = str(_event_data(deltas[0]).get("snapshot") or "")

    return {
        "result_success": bool(getattr(result, "success", False)),
        "result_exit_reason": str(getattr(result, "exit_reason", "") or ""),
        "tool_calls_used": int(getattr(result, "tool_calls_used", 0) or 0),
        "turn_count": len(turns),
        "turn": _event_data(turns[0]) if turns else {},
        "judge_count": len(verdicts),
        "judge": _event_data(verdicts[0]) if verdicts else {},
        "loop_started_count": len(started),
        "loop_completed_count": len(completed),
        "response_text": latest_snapshot,
    }


def _validate_agent_loop_evidence(evidence: dict[str, Any]) -> None:
    if not evidence.get("result_success"):
        raise HiveAdapterError(
            "Hive AgentLoop did not complete successfully; no benchmark evidence accepted"
        )
    if evidence.get("tool_calls_used") != 0:
        raise HiveAdapterError(
            "controlled Hive AgentLoop benchmark unexpectedly used tools"
        )
    if evidence.get("turn_count") != 1:
        raise HiveAdapterError(
            "controlled Hive AgentLoop benchmark must prove exactly one LLM turn"
        )
    if evidence.get("loop_started_count") != 1 or evidence.get("loop_completed_count") != 1:
        raise HiveAdapterError(
            "Hive AgentLoop lifecycle events are incomplete"
        )
    if evidence.get("judge_count") != 1:
        raise HiveAdapterError(
            "controlled Hive AgentLoop benchmark must prove exactly one judge verdict"
        )
    judge = evidence.get("judge")
    if not isinstance(judge, dict):
        raise HiveAdapterError("Hive AgentLoop judge evidence is malformed")
    if judge.get("action") != "ACCEPT" or judge.get("judge_type") != "implicit":
        raise HiveAdapterError(
            "Hive AgentLoop did not finish through the expected implicit ACCEPT path"
        )
    turn = evidence.get("turn")
    if not isinstance(turn, dict) or not turn.get("model"):
        raise HiveAdapterError("Hive AgentLoop LLM turn metadata is incomplete")


async def _execute_hive_agent_loop(
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

    execution_id = f"advisor-agent-loop-{uuid.uuid4().hex[:16]}"
    bus = EventBus(max_history=500)
    spec = AgentSpec(
        id="advisor_benchmark",
        name="AI Model Advisor AgentLoop Benchmark",
        description=(
            "Controlled no-tool benchmark path for measuring Hive AgentLoop overhead "
            "and model behavior."
        ),
        system_prompt=(
            "Complete the user's task directly and concisely. "
            "No tools are available. Return the task result only."
        ),
        output_keys=[],
        tools=[],
        tool_access_policy="none",
        skip_judge=False,
    )
    ctx = AgentContext(
        runtime=None,  # AgentLoop itself does not dereference DecisionTracker.
        agent_id=spec.id,
        agent_spec=spec,
        input_data={"task": task},
        llm=provider,
        available_tools=[],
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
            tool_call_budget=0,
            tool_call_lifetime_budget=0,
        ),
    )

    started = time.perf_counter()
    result = await asyncio.wait_for(loop.execute(ctx), timeout=timeout_seconds)
    latency_seconds = time.perf_counter() - started
    evidence = _collect_agent_loop_evidence(bus, EventType, execution_id, result)
    return evidence, request_reader(), litellm_version, latency_seconds


def _build_result(
    configuration: dict[str, str],
    evidence: dict[str, Any],
    *,
    litellm_version: str,
    latency_seconds: float,
) -> dict[str, Any]:
    turn = evidence["turn"]
    result: dict[str, Any] = {
        "schema_version": _RUNNER_SCHEMA_VERSION,
        "applied_configuration": configuration,
        "outcome": "partial",
        "response_text": str(evidence.get("response_text") or ""),
        "resolved_model": turn.get("model"),
        "provider_status": turn.get("stop_reason"),
        "input_tokens": turn.get("input_tokens"),
        "output_tokens": turn.get("output_tokens"),
        "cached_tokens": turn.get("cached_tokens"),
        "cache_creation_tokens": turn.get("cache_creation_tokens"),
        "latency_seconds": round(latency_seconds, 6),
        "transport": "hive_agent_loop",
        "litellm_version": litellm_version,
        "agent_loop_evidence": {
            "turn_count": evidence["turn_count"],
            "judge_count": evidence["judge_count"],
            "judge_action": evidence["judge"].get("action"),
            "judge_type": evidence["judge"].get("judge_type"),
            "loop_started_count": evidence["loop_started_count"],
            "loop_completed_count": evidence["loop_completed_count"],
            "exit_reason": evidence.get("result_exit_reason"),
            "tool_calls_used": evidence.get("tool_calls_used"),
        },
        "note": (
            "Hive AgentLoop completed through a no-tool, single-turn, implicit-ACCEPT "
            "lifecycle and wire configuration was verified; benchmark outcome still "
            "requires the external deterministic judge"
        ),
    }
    cost = turn.get("cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        result["cost_usd"] = float(cost)
    credits = turn.get("credits")
    if isinstance(credits, (int, float)):
        result["credits"] = float(credits)
    return {key: value for key, value in result.items() if value is not None}


def run_hive_agent_loop_adapter(
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
    execute = executor or _execute_hive_agent_loop

    previous_hive_home = os.environ.get("HIVE_HOME")
    with tempfile.TemporaryDirectory(prefix="ai-model-advisor-agent-loop-") as temp_home:
        os.environ["HIVE_HOME"] = temp_home
        try:
            evidence, wire_request, litellm_version, latency_seconds = asyncio.run(
                execute(
                    task,
                    configuration,
                    timeout_seconds,
                    max_output_tokens,
                )
            )
            _validate_agent_loop_evidence(evidence)
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


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            "Run one controlled no-tool experiment through Hive's real AgentLoop. "
            "Consumes runner JSON on stdin and emits runner JSON on stdout."
        )
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        result = run_hive_agent_loop_adapter(payload)
    except (HiveAdapterError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"hive agent-loop adapter error: {str(exc)[:1200]}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - infrastructure failures are never evidence
        print(
            f"hive agent-loop adapter infrastructure error: {str(exc)[:1200]}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
