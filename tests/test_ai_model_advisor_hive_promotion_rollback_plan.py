from __future__ import annotations

from copy import deepcopy

from tools.ai_model_advisor.hive_promotion_rollback_plan import (
    build_hive_promotion_rollback_plan,
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


def test_rollback_plan_builds_exact_manual_patch() -> None:
    plan = build_hive_promotion_rollback_plan(_status())

    assert plan["state"] == "ready_for_manual_rollback"
    assert plan["ready_for_manual_rollback"] is True
    assert plan["automatic_config_mutation"] is False
    assert plan["automatic_rollback"] is False
    assert plan["merge_patch"] == {
        "llm": {
            "provider": "test",
            "model": "current-model",
            "reasoning_effort": "medium",
        }
    }
    assert plan["operations"][0]["active_change_id"] == "debugging-change"
    assert plan["preconditions"][0]["section"] == "llm"
    assert plan["preconditions"][0]["expected_current_sha256"]
    assert plan["rollback_plan_sha256"]


def test_rollback_plan_uses_null_to_restore_default_effort() -> None:
    status = _status()
    status["routes"][0]["rollback_target"]["effort"] = "default"

    plan = build_hive_promotion_rollback_plan(status)

    assert plan["ready_for_manual_rollback"] is True
    assert plan["merge_patch"]["llm"]["reasoning_effort"] is None


def test_rollback_plan_blocks_drifted_status() -> None:
    status = _status()
    status["status"] = "drifted"
    status["routes"][0]["live_state"] = "drifted"
    status["routes"][0]["manual_rollback_ready"] = False

    plan = build_hive_promotion_rollback_plan(status)

    assert plan["state"] == "blocked"
    assert plan["ready_for_manual_rollback"] is False
    assert plan["merge_patch"] == {}
    assert plan["blockers"][0]["code"] == "status_not_eligible"


def test_rollback_plan_blocks_ambiguous_physical_section() -> None:
    status = _status()
    second = deepcopy(status["routes"][0])
    second["category"] = "coding"
    second["active_change_id"] = "coding-change"
    second["rollback_target"] = _config("different-target", "medium")
    status["routes"].append(second)

    plan = build_hive_promotion_rollback_plan(status)

    assert plan["state"] == "blocked"
    assert plan["ready_for_manual_rollback"] is False
    assert plan["merge_patch"] == {}
    assert plan["blockers"][0]["code"] == "ambiguous_physical_section"
    assert plan["blockers"][0]["section"] == "llm"


def test_rollback_plan_allows_attention_when_current_route_still_verified() -> None:
    status = _status()
    status["status"] = "attention"
    status["operator_action"] = "review_pending_promotions"
    status["routes"][0]["pending_promotions"] = [
        {
            "change_id": "future-change",
            "after": _config("future-model"),
        }
    ]

    plan = build_hive_promotion_rollback_plan(status)

    assert plan["ready_for_manual_rollback"] is True
    assert plan["state"] == "ready_for_manual_rollback"
