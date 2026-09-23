from __future__ import annotations

from copy import deepcopy

from tools.ai_model_advisor.hive_promotion_reconcile import (
    build_hive_promotion_reconciliation,
)


def _config(model: str = "candidate-model", effort: str = "high") -> dict[str, str]:
    return {
        "provider": "test",
        "model_id": model,
        "effort": effort,
        "execution_mode": "single",
    }


def _registry() -> dict:
    current = _config()
    rollback = _config("current-model", "high")
    return {
        "schema_version": 1,
        "host": "hive",
        "status": "ready",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "routes": [
            {
                "category": "debugging",
                "route": "queen",
                "status": "current_verified",
                "current_verified_config": current,
                "active_change_id": "debugging-change",
                "rollback_status": "verified",
                "rollback_target": rollback,
                "pending_previews": [],
                "evidence": [],
            }
        ],
        "blockers": [],
    }


def test_reconciliation_verifies_exact_current_route_and_rollback() -> None:
    report = build_hive_promotion_reconciliation(
        _registry(),
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
            }
        },
    )
    assert report["status"] == "verified"
    assert report["verified"] is True
    route = report["routes"][0]
    assert route["state"] == "verified"
    assert route["manual_rollback_ready"] is True
    assert route["rollback_target"]["model_id"] == "current-model"


def test_reconciliation_reports_model_and_effort_drift() -> None:
    report = build_hive_promotion_reconciliation(
        _registry(),
        {
            "llm": {
                "provider": "test",
                "model": "unexpected-model",
                "reasoning_effort": "low",
            }
        },
    )
    assert report["status"] == "drifted"
    assert report["verified"] is False
    assert report["drift_count"] == 1
    route = report["routes"][0]
    assert route["state"] == "drifted"
    assert route["manual_rollback_ready"] is False
    assert any("model expected" in item for item in route["differences"])
    assert any("reasoning_effort expected" in item for item in route["differences"])


def test_reconciliation_blocks_blocked_registry() -> None:
    registry = deepcopy(_registry())
    registry["status"] = "blocked"
    registry["blockers"] = [{"code": "conflict", "reason": "conflicting evidence"}]

    report = build_hive_promotion_reconciliation(registry, {})

    assert report["status"] == "blocked_registry"
    assert report["verified"] is False
    assert report["routes"] == []
    assert report["blockers"][0]["code"] == "conflict"


def test_reconciliation_does_not_leak_hive_secrets() -> None:
    report = build_hive_promotion_reconciliation(
        _registry(),
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
                "api_key": "SECRET_KEY_MUST_NOT_LEAK",
                "api_base": "https://secret.example/v1",
            },
            "unrelated": {"token": "ANOTHER_SECRET"},
        },
    )

    raw = str(report)
    assert "SECRET_KEY_MUST_NOT_LEAK" not in raw
    assert "ANOTHER_SECRET" not in raw
    assert "secret.example" not in raw


def test_default_effort_requires_absent_reasoning_effort_key() -> None:
    registry = _registry()
    registry["routes"][0]["current_verified_config"]["effort"] = "default"

    exact = build_hive_promotion_reconciliation(
        registry,
        {"llm": {"provider": "test", "model": "candidate-model"}},
    )
    drifted = build_hive_promotion_reconciliation(
        registry,
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": None,
            }
        },
    )

    assert exact["verified"] is True
    assert drifted["status"] == "drifted"
