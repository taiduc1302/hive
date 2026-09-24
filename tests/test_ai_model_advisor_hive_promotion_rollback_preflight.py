from __future__ import annotations

from copy import deepcopy

import pytest

from tools.ai_model_advisor.hive_promotion_rollback_plan import (
    build_hive_promotion_rollback_plan,
)
from tools.ai_model_advisor.hive_promotion_rollback_preflight import (
    HivePromotionRollbackPreflightError,
    build_hive_promotion_rollback_preflight,
)


def _config(model_id: str, effort: str = "high") -> dict[str, str]:
    return {
        "provider": "test",
        "model_id": model_id,
        "effort": effort,
        "execution_mode": "single",
    }


def _status() -> dict:
    return {
        "schema_version": 1,
        "host": "hive",
        "status": "verified",
        "operator_action": "none",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "routes": [
            {
                "category": "debugging",
                "route": "queen",
                "registry_status": "current_verified",
                "live_state": "verified",
                "current_verified_config": _config("candidate-model"),
                "actual_config": {
                    "provider": "test",
                    "model": "candidate-model",
                    "reasoning_effort": "high",
                    "reasoning_effort_key_present": True,
                },
                "active_change_id": "debugging-change",
                "rollback_status": "verified",
                "rollback_target": _config("current-model", "medium"),
                "manual_rollback_ready": True,
                "pending_promotions": [],
                "differences": [],
            }
        ],
        "blockers": [],
    }


def _plan() -> dict:
    return build_hive_promotion_rollback_plan(_status())


def test_preflight_accepts_fresh_current_config() -> None:
    report = build_hive_promotion_rollback_preflight(
        _plan(),
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
            }
        },
    )

    assert report["state"] == "ready_for_manual_edit"
    assert report["ready_for_manual_edit"] is True
    assert report["stale_precondition_count"] == 0
    assert report["merge_patch"]["llm"]["model"] == "current-model"
    assert report["precondition_checks"][0]["matches"] is True
    assert report["rollback_preflight_sha256"]


def test_preflight_blocks_stale_current_config() -> None:
    report = build_hive_promotion_rollback_preflight(
        _plan(),
        {
            "llm": {
                "provider": "test",
                "model": "unexpected-model",
                "reasoning_effort": "low",
            }
        },
    )

    assert report["state"] == "blocked_stale_rollback_plan"
    assert report["ready_for_manual_edit"] is False
    assert report["stale_precondition_count"] == 1
    assert report["merge_patch"] == {}
    assert report["precondition_checks"][0]["matches"] is False


def test_preflight_does_not_leak_secrets() -> None:
    report = build_hive_promotion_rollback_preflight(
        _plan(),
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
                "api_key": "ROLLBACK_PREFLIGHT_SECRET",
                "api_base": "https://secret.example/v1",
            },
            "other": {"token": "OTHER_SECRET"},
        },
    )

    raw = str(report)
    assert report["ready_for_manual_edit"] is True
    assert "ROLLBACK_PREFLIGHT_SECRET" not in raw
    assert "OTHER_SECRET" not in raw
    assert "secret.example" not in raw


def test_preflight_rejects_tampered_plan_self_hash() -> None:
    plan = deepcopy(_plan())
    plan["merge_patch"]["llm"]["model"] = "tampered-target"

    with pytest.raises(
        HivePromotionRollbackPreflightError,
        match="rollback_plan_sha256 is invalid",
    ):
        build_hive_promotion_rollback_preflight(
            plan,
            {
                "llm": {
                    "provider": "test",
                    "model": "candidate-model",
                    "reasoning_effort": "high",
                }
            },
        )


def test_preflight_preserves_human_approval_boundary() -> None:
    report = build_hive_promotion_rollback_preflight(
        _plan(),
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
            }
        },
    )

    assert report["safe_to_auto_apply"] is False
    assert report["automatic_config_mutation"] is False
    assert report["automatic_rollback"] is False
    assert report["requires_human_approval"] is True
