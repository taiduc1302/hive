from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_RUNNER_SCHEMA_VERSION = 1
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_TOKENS = 32768
_SUPPORTED_PROVIDERS = {"openai", "anthropic"}


class HiveAdapterError(RuntimeError):
    """Raised when Hive cannot prove that the requested configuration reached the wire."""


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HiveAdapterError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HiveAdapterError(f"{name} must be an integer") from exc
    if value <= 0:
        raise HiveAdapterError(f"{name} must be > 0")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise HiveAdapterError(f"{name} must be numeric") from exc
    if value <= 0:
        raise HiveAdapterError(f"{name} must be > 0")
    return value


def validate_runner_payload(payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, dict):
        raise HiveAdapterError("runner payload must be a JSON object")
    if payload.get("schema_version") != _RUNNER_SCHEMA_VERSION:
        raise HiveAdapterError(f"runner schema_version must be {_RUNNER_SCHEMA_VERSION}")
    if payload.get("acceptance_mode") != "external_judge":
        raise HiveAdapterError(
            "Hive transport requires acceptance_mode=external_judge; a successful provider call is not benchmark success"
        )

    task = _required_string(payload, "task")
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise HiveAdapterError("configuration must be a JSON object")
    normalized = {
        key: _required_string(configuration, key)
        for key in ("provider", "model_id", "effort", "execution_mode")
    }
    if normalized["execution_mode"] != "single":
        raise HiveAdapterError(
            "Hive LiteLLM adapter only supports execution_mode=single; "
            "AgentLoop, colony, subagent, and Work-style orchestration need separate adapters"
        )
    if normalized["provider"] not in _SUPPORTED_PROVIDERS:
        raise HiveAdapterError(f"unsupported Hive LiteLLM provider: {normalized['provider']}")
    return task, normalized


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _provider_model(configuration: dict[str, str]) -> str:
    provider = configuration["provider"]
    model_id = configuration["model_id"]
    prefix = f"{provider}/"
    return model_id if model_id.startswith(prefix) else f"{prefix}{model_id}"


def _provider_api_key(configuration: dict[str, str]) -> str:
    env_name = "OPENAI_API_KEY" if configuration["provider"] == "openai" else "ANTHROPIC_API_KEY"
    api_key = os.getenv(env_name)
    if not api_key:
        raise HiveAdapterError(f"{env_name} is required for Hive LiteLLM experiments")
    return api_key


def _load_hive_transport(
    configuration: dict[str, str],
    timeout_seconds: float,
) -> tuple[Any, Callable[[], dict[str, Any] | None], str]:
    core_dir = _repo_root() / "core"
    core_text = str(core_dir)
    if core_text not in sys.path:
        sys.path.insert(0, core_text)

    try:
        import litellm as litellm_package
        from framework.llm import litellm as hive_litellm
        from framework.llm.litellm import LiteLLMProvider
    except ImportError as exc:
        raise HiveAdapterError(
            "Hive framework/LiteLLM is not importable; install the repository workspace before using this adapter"
        ) from exc

    provider_kwargs: dict[str, Any] = {
        "model": _provider_model(configuration),
        "api_key": _provider_api_key(configuration),
        "timeout": timeout_seconds,
    }
    if configuration["effort"] != "default":
        provider_kwargs["reasoning_effort"] = configuration["effort"]
    provider = LiteLLMProvider(**provider_kwargs)
    version = str(getattr(litellm_package, "__version__", "unknown"))
    return provider, hive_litellm._last_llm_request.get, version


def _flatten_request_body(request: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise HiveAdapterError("Hive did not capture the outgoing LiteLLM request")
    body = request.get("body")
    if not isinstance(body, dict):
        raise HiveAdapterError("Hive request capture does not contain a JSON request body")
    merged = dict(body)
    extra_body = merged.pop("extra_body", None)
    if isinstance(extra_body, dict):
        merged.update(extra_body)
    return merged


def _wire_effort(provider: str, body: dict[str, Any]) -> Any:
    if provider == "openai":
        actual_effort = body.get("reasoning_effort")
        reasoning = body.get("reasoning")
        if actual_effort is None and isinstance(reasoning, dict):
            actual_effort = reasoning.get("effort")
        return actual_effort
    output_config = body.get("output_config")
    return output_config.get("effort") if isinstance(output_config, dict) else None


def _verify_wire_configuration(configuration: dict[str, str], request: dict[str, Any] | None) -> None:
    body = _flatten_request_body(request)
    model_id = configuration["model_id"]
    provider_model = _provider_model(configuration)
    actual_model = body.get("model")
    if actual_model not in {model_id, provider_model}:
        raise HiveAdapterError(
            f"Hive wire model mismatch: expected {model_id!r} (or {provider_model!r}), got {actual_model!r}"
        )

    expected_effort = configuration["effort"]
    actual_effort = _wire_effort(configuration["provider"], body)
    if expected_effort == "default":
        if actual_effort is not None:
            raise HiveAdapterError(
                "Hive/LiteLLM sent an explicit reasoning effort for effort=default; "
                f"got {actual_effort!r}"
            )
        return
    if actual_effort != expected_effort:
        raise HiveAdapterError(
            "Hive/LiteLLM did not prove the requested reasoning effort on the post-transform wire body; "
            f"expected {expected_effort!r}, got {actual_effort!r}"
        )


def _response_result(
    configuration: dict[str, str],
    response: Any,
    *,
    latency_seconds: float,
    litellm_version: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": _RUNNER_SCHEMA_VERSION,
        "applied_configuration": configuration,
        "outcome": "partial",
        "response_text": str(getattr(response, "content", "") or ""),
        "resolved_model": getattr(response, "model", None),
        "provider_status": getattr(response, "stop_reason", None),
        "input_tokens": getattr(response, "input_tokens", None),
        "output_tokens": getattr(response, "output_tokens", None),
        "cached_tokens": getattr(response, "cached_tokens", None),
        "cache_creation_tokens": getattr(response, "cache_creation_tokens", None),
        "latency_seconds": round(latency_seconds, 6),
        "transport": "hive_litellm",
        "litellm_version": litellm_version,
        "note": "Hive LiteLLM transport completed and wire configuration was verified; outcome requires deterministic judge",
    }
    cost_usd = getattr(response, "cost_usd", None)
    if isinstance(cost_usd, (int, float)) and cost_usd > 0:
        result["cost_usd"] = float(cost_usd)
    credits = getattr(response, "credits", None)
    if isinstance(credits, (int, float)):
        result["credits"] = float(credits)
    return {key: value for key, value in result.items() if value is not None}


def run_hive_adapter(
    payload: dict[str, Any],
    *,
    transport_loader: Callable[
        [dict[str, str], float], tuple[Any, Callable[[], dict[str, Any] | None], str]
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
    loader = transport_loader or _load_hive_transport

    previous_hive_home = os.environ.get("HIVE_HOME")
    with tempfile.TemporaryDirectory(prefix="ai-model-advisor-hive-") as temp_home:
        os.environ["HIVE_HOME"] = temp_home
        try:
            provider, request_reader, litellm_version = loader(configuration, timeout_seconds)
            started = time.perf_counter()
            response = provider.complete(
                messages=[{"role": "user", "content": task}],
                max_tokens=max_output_tokens,
                max_retries=0,
            )
            latency_seconds = time.perf_counter() - started
            _verify_wire_configuration(configuration, request_reader())
        finally:
            if previous_hive_home is None:
                os.environ.pop("HIVE_HOME", None)
            else:
                os.environ["HIVE_HOME"] = previous_hive_home

    return _response_result(
        configuration,
        response,
        latency_seconds=latency_seconds,
        litellm_version=litellm_version,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = run_hive_adapter(payload)
    except (HiveAdapterError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"hive adapter error: {str(exc)[:1200]}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - provider errors are infrastructure failures, never benchmark evidence
        print(f"hive adapter infrastructure error: {str(exc)[:1200]}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
