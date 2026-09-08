from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_RUNNER_SCHEMA_VERSION = 1
_OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
_ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_TOKENS = 32768


class ProviderAdapterError(RuntimeError):
    """Raised when a direct provider request cannot produce trustworthy transport data."""


@dataclass(frozen=True)
class ProviderRequest:
    provider: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    configuration: dict[str, str]


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderAdapterError(f"{key} must be a non-empty string")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderAdapterError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ProviderAdapterError(f"{name} must be > 0")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ProviderAdapterError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ProviderAdapterError(f"{name} must be > 0")
    return value


def validate_runner_payload(payload: dict[str, Any]) -> tuple[str, dict[str, str], str]:
    if not isinstance(payload, dict):
        raise ProviderAdapterError("runner payload must be a JSON object")
    if payload.get("schema_version") != _RUNNER_SCHEMA_VERSION:
        raise ProviderAdapterError(f"runner schema_version must be {_RUNNER_SCHEMA_VERSION}")
    if payload.get("acceptance_mode") != "external_judge":
        raise ProviderAdapterError(
            "direct provider adapter requires acceptance_mode=external_judge; "
            "HTTP completion is not benchmark success"
        )

    task = _required_string(payload, "task")
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise ProviderAdapterError("configuration must be a JSON object")
    normalized = {
        key: _required_string(configuration, key)
        for key in ("provider", "model_id", "effort", "execution_mode")
    }
    if normalized["execution_mode"] != "single":
        raise ProviderAdapterError(
            "direct provider API adapter only supports execution_mode=single; "
            "orchestration modes require a host-specific adapter"
        )
    if normalized["provider"] not in {"openai", "anthropic"}:
        raise ProviderAdapterError(
            f"unsupported direct API provider: {normalized['provider']}"
        )
    return task, normalized, str(payload.get("task_sha256") or "")


def build_provider_request(payload: dict[str, Any]) -> ProviderRequest:
    task, config, _task_hash = validate_runner_payload(payload)
    max_output_tokens = _positive_int_env(
        "AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS",
        _DEFAULT_MAX_OUTPUT_TOKENS,
    )

    if config["provider"] == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ProviderAdapterError("OPENAI_API_KEY is required for OpenAI experiments")
        return ProviderRequest(
            provider="openai",
            url=_OPENAI_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            body={
                "model": config["model_id"],
                "input": task,
                "reasoning": {"effort": config["effort"]},
                "max_output_tokens": max_output_tokens,
                "store": False,
            },
            configuration=config,
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderAdapterError("ANTHROPIC_API_KEY is required for Anthropic experiments")
    return ProviderRequest(
        provider="anthropic",
        url=_ANTHROPIC_ENDPOINT,
        headers={
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        },
        body={
            "model": config["model_id"],
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": task}],
            "output_config": {"effort": config["effort"]},
        },
        configuration=config,
    )


def _http_json(request: ProviderRequest, timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        request.url,
        data=data,
        headers=request.headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = ""
        message = f"{request.provider} API returned HTTP {exc.code}"
        if detail:
            message += f": {detail[:800]}"
        raise ProviderAdapterError(message) from exc
    except urllib.error.URLError as exc:
        raise ProviderAdapterError(f"{request.provider} API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderAdapterError(f"{request.provider} API request timed out") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderAdapterError(f"{request.provider} API returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderAdapterError(f"{request.provider} API response must be an object")
    return parsed


def _openai_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "output_text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _anthropic_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    content = response.get("content")
    if not isinstance(content, list):
        return ""
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _anthropic_normalized_usage(usage: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    uncached = _nonnegative_int(usage.get("input_tokens"))
    cache_read = _nonnegative_int(usage.get("cache_read_input_tokens"))
    cache_creation = _nonnegative_int(usage.get("cache_creation_input_tokens"))
    known_parts = [value for value in (uncached, cache_read, cache_creation) if value is not None]
    total_input = sum(known_parts) if known_parts else None
    return total_input, cache_read, cache_creation


def parse_provider_response(
    request: ProviderRequest,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Normalize transport output while leaving benchmark correctness to a judge."""
    if request.provider == "openai":
        if response.get("error"):
            raise ProviderAdapterError("OpenAI response contains an error object")
        status = response.get("status")
        if status == "failed":
            raise ProviderAdapterError("OpenAI response status is failed")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        input_details = (
            usage.get("input_tokens_details")
            if isinstance(usage.get("input_tokens_details"), dict)
            else {}
        )
        result = {
            "schema_version": _RUNNER_SCHEMA_VERSION,
            "applied_configuration": request.configuration,
            "outcome": "partial",
            "response_text": _openai_output_text(response),
            "provider_response_id": response.get("id"),
            "provider_status": status,
            "resolved_model": response.get("model"),
            "input_tokens": _nonnegative_int(usage.get("input_tokens")),
            "output_tokens": _nonnegative_int(usage.get("output_tokens")),
            "cached_tokens": _nonnegative_int(input_details.get("cached_tokens")),
            "cache_creation_tokens": _nonnegative_int(input_details.get("cache_write_tokens")),
            "note": "direct OpenAI transport completed; outcome requires deterministic judge",
        }
    else:
        if response.get("type") == "error" or response.get("error"):
            raise ProviderAdapterError("Anthropic response contains an error object")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        input_tokens, cached_tokens, cache_creation_tokens = _anthropic_normalized_usage(usage)
        result = {
            "schema_version": _RUNNER_SCHEMA_VERSION,
            "applied_configuration": request.configuration,
            "outcome": "partial",
            "response_text": _anthropic_output_text(response),
            "provider_response_id": response.get("id"),
            "provider_status": response.get("stop_reason"),
            "resolved_model": response.get("model"),
            "input_tokens": input_tokens,
            "output_tokens": _nonnegative_int(usage.get("output_tokens")),
            "cached_tokens": cached_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "note": "direct Anthropic transport completed; outcome requires deterministic judge",
        }

    return {key: value for key, value in result.items() if value is not None}


def run_provider_adapter(payload: dict[str, Any]) -> dict[str, Any]:
    request = build_provider_request(payload)
    timeout_seconds = _positive_float_env(
        "AI_MODEL_ADVISOR_PROVIDER_TIMEOUT_SECONDS",
        _DEFAULT_TIMEOUT_SECONDS,
    )
    response = _http_json(request, timeout_seconds)
    return parse_provider_response(request, response)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = run_provider_adapter(payload)
    except (ProviderAdapterError, json.JSONDecodeError, TypeError) as exc:
        print(f"provider adapter error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
