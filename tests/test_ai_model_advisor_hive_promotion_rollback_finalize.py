from __future__ import annotations

import json
from argparse import Namespace
from copy import deepcopy

import pytest

from tools.ai_model_advisor.cli import command_hive_promotion_rollback_finalize
from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_lifecycle import (
    build_hive_promotion_lifecycle,
)
from tools.ai_model_advisor.hive_promotion_preview import (
    build_hive_promotion_preview,
)
from tools.ai_model_advisor.hive_promotion_receipt import (
    build_hive_promotion_receipt,
)
from tools.ai_model_advisor.hive_promotion_rollback_finalize import (
    HivePromotionRollbackFinalizeError,
    build_hive_promotion_rollback_finalization,
)
from tools.ai_model_advisor.hive_promotion_rollback_plan import (
    build_hive_promotion_rollback_plan,
)
from tools.ai_model_advisor.hive_promotion_rollback_preflight import (
    build_hive_promotion_rollback_preflight,
)


def _config(model_id: str, effort: str) -> dict[str, str]:
    return {
        "provider": "openai",
        "model_id": model_id,
        "effort": effort,
        "execution_mode": "single",
    }


def _review() -> dict:
    before = _config("gpt-5.6-sol", "medium")
    after = _config("gpt-6-astra", "high")
    return {
        "reviews": [
            {
                "category": "coding",
                "state": "ready_for_manual_edit",
                "safe_to_apply": False,
                "requires_human_approval": True,
                "manual_change": {
                    "change_id": "coding-finalize-test",
                    "category": "coding",
                    "before": before,
                    "after": after,
                    "rollback_to": before,
                },
            }
        ],
        "safe_to_apply": False,
        "automatic_policy_mutation": False,
        "automatic_rollback": False,
    }


def _preview() -> dict:
    return build_hive_promotion_preview(_review(), category="coding", scope="queen")


def _capabilities() -> dict:
    return {
        "schema_version": 1,
        "host": "hive",
        "litellm": {"installed_version": "1.83.4", "versions_match": True},
        "transport": {"single_call_evidence_ready": True},
        "native_config": {"reasoning_effort_passthrough": True},
    }


def _evidence() -> dict:
    return {
        "schema_version": 1,
        "transport": "hive_litellm",
        "litellm_version": "1.83.4",
        "applied_configuration": _config("gpt-6-astra", "high"),
        "outcome": "partial",
    }


def _applied_receipt(preview: dict) -> dict:
    return build_hive_promotion_receipt(
        preview,
        {"llm": {"provider": "openai", "model": "gpt-6-astra", "reasoning_effort": "high"}},
    )


def _lifecycle(preview: dict) -> dict:
    gate = build_hive_promotion_gate(preview, _capabilities(), _evidence())
    return build_hive_promotion_lifecycle(
        _review(),
        preview,
        gate,
        _applied_receipt(preview),
    )


def _rollback_receipt(preview: dict) -> dict:
    return build_hive_promotion_receipt(
        preview,
        {"llm": {"provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium"}},
        previous_receipt=_applied_receipt(preview),
    )


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
                "category": "coding",
                "route": "queen",
                "registry_status": "current_verified",
                "live_state": "verified",
                "current_verified_config": _config("gpt-6-astra", "high"),
                "actual_config": {
                    "provider": "openai",
                    "model": "gpt-6-astra",
                    "reasoning_effort": "high",
                    "reasoning_effort_key_present": True,
                },
                "active_change_id": "coding-finalize-test",
                "rollback_status": "verified",
                "rollback_target": _config("gpt-5.6-sol", "medium"),
                "manual_rollback_ready": True,
                "pending_promotions": [],
                "differences": [],
            }
        ],
        "blockers": [],
    }


def _plan() -> dict:
    return build_hive_promotion_rollback_plan(_status())


def _preflight(plan: dict) -> dict:
    return build_hive_promotion_rollback_preflight(
        plan,
        {"llm": {"provider": "openai", "model": "gpt-6-astra", "reasoning_effort": "high"}},
    )


def test_finalization_binds_verified_rollback_to_fresh_preflight() -> None:
    preview = _preview()
    plan = _plan()
    report = build_hive_promotion_rollback_finalization(
        preview,
        _lifecycle(preview),
        plan,
        _preflight(plan),
        _rollback_receipt(preview),
    )

    assert report["state"] == "rollback_verified_from_fresh_preflight"
    assert report["rollback_verified_from_fresh_preflight"] is True
    assert report["rollback_audit_state"] == "rolled_back_verified"
    assert report["automatic_config_mutation"] is False
    assert report["automatic_rollback"] is False
    assert report["artifact_hashes"]["rollback_preflight_sha256"]


def test_finalization_rejects_tampered_preflight_hash() -> None:
    preview = _preview()
    plan = _plan()
    preflight = deepcopy(_preflight(plan))
    preflight["merge_patch"]["llm"]["model"] = "tampered"

    with pytest.raises(
        HivePromotionRollbackFinalizeError,
        match="rollback_preflight_sha256 is invalid",
    ):
        build_hive_promotion_rollback_finalization(
            preview,
            _lifecycle(preview),
            plan,
            preflight,
            _rollback_receipt(preview),
        )


def test_finalization_blocks_when_rollback_audit_does_not_verify() -> None:
    preview = _preview()
    plan = _plan()
    still_applied = build_hive_promotion_receipt(
        preview,
        {"llm": {"provider": "openai", "model": "gpt-6-astra", "reasoning_effort": "high"}},
        previous_receipt=_applied_receipt(preview),
    )

    report = build_hive_promotion_rollback_finalization(
        preview,
        _lifecycle(preview),
        plan,
        _preflight(plan),
        still_applied,
    )

    assert report["state"] == "blocked_rollback_audit"
    assert report["rollback_verified_from_fresh_preflight"] is False
    assert report["rollback_audit_state"] == "rollback_not_applied"


def test_finalization_rejects_stale_preflight() -> None:
    preview = _preview()
    plan = _plan()
    stale = build_hive_promotion_rollback_preflight(
        plan,
        {"llm": {"provider": "openai", "model": "unexpected", "reasoning_effort": "low"}},
    )

    with pytest.raises(
        HivePromotionRollbackFinalizeError,
        match="ready_for_manual_edit",
    ):
        build_hive_promotion_rollback_finalization(
            preview,
            _lifecycle(preview),
            plan,
            stale,
            _rollback_receipt(preview),
        )


def test_unified_cli_writes_preflight_bound_finalization(tmp_path) -> None:
    preview = _preview()
    lifecycle = _lifecycle(preview)
    plan = _plan()
    preflight = _preflight(plan)
    receipt = _rollback_receipt(preview)

    paths = {}
    for name, payload in (
        ("preview", preview),
        ("lifecycle", lifecycle),
        ("plan", plan),
        ("preflight", preflight),
        ("receipt", receipt),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    markdown_path = tmp_path / "finalization.md"
    json_path = tmp_path / "finalization.json"
    code = command_hive_promotion_rollback_finalize(
        Namespace(
            promotion_preview=str(paths["preview"]),
            applied_lifecycle=str(paths["lifecycle"]),
            rollback_plan=str(paths["plan"]),
            rollback_preflight=str(paths["preflight"]),
            rollback_receipt=str(paths["receipt"]),
            output=str(markdown_path),
            json_output=str(json_path),
            require_verified=True,
        )
    )

    assert code == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["rollback_verified_from_fresh_preflight"] is True
    assert "Hive Rollback Finalization" in markdown_path.read_text(encoding="utf-8")
