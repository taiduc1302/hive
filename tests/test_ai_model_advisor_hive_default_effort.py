from __future__ import annotations

import pytest

from tools.ai_model_advisor.hive_litellm_adapter import (
    HiveAdapterError,
    _verify_wire_configuration,
)


def _configuration(effort: str = "default") -> dict[str, str]:
    return {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "effort": effort,
        "execution_mode": "single",
    }


def test_hive_default_effort_accepts_wire_body_without_explicit_effort():
    request = {
        "body": {
            "model": "anthropic/claude-haiku-4-5-20251001",
            "messages": [{"role": "user", "content": "benchmark"}],
        }
    }

    _verify_wire_configuration(_configuration(), request)


def test_hive_default_effort_rejects_accidental_anthropic_effort():
    request = {
        "body": {
            "model": "anthropic/claude-haiku-4-5-20251001",
            "output_config": {"effort": "high"},
        }
    }

    with pytest.raises(HiveAdapterError, match="explicit reasoning effort"):
        _verify_wire_configuration(_configuration(), request)


def test_hive_explicit_effort_still_requires_wire_proof():
    request = {
        "body": {
            "model": "anthropic/claude-haiku-4-5-20251001",
            "output_config": {"effort": "medium"},
        }
    }

    _verify_wire_configuration(_configuration("medium"), request)
