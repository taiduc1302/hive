from __future__ import annotations

from copy import deepcopy

import pytest

from tools.ai_model_advisor.hive_promotion_checkpoint import (
    build_hive_promotion_checkpoint,
)
from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_journal import (
    HivePromotionJournalError,
    append_hive_promotion_journal,
    build_hive_promotion_journal,
)
from tools.ai_model_advisor.hive_promotion_lifecycle import (
    build_hive_promotion_lifecycle,
)
from tools.ai_model_advisor.hive_promotion_preview import (
    build_hive_promotion_preview,
)
from tools.ai_model_advisor.hive_promotion_receipt import (
    build_hive_promotion_receipt,
)
from tools.ai_model_advisor.hive_promotion_registry import (
    build_hive_promotion_registry,
)
from tools.ai_model_advisor.hive_promotion_rollback import (
    build_hive_promotion_rollback_audit,
)
from tools.ai_model_advisor.hive_promotion_rollback_finalize import (
    build_hive_promotion_rollback_finalization,
)
from tools.ai_model_advisor.hive_promotion_rollback_plan import (
    build_hive_promotion_rollback_plan,
)
from tools.ai_model_advisor.hive_promotion_rollback_preflight import (
    build_hive_promotion_rollback_preflight,
)
from tools.ai_model_advisor.hive_promotion_status import (
    build_hive_promotion_status,
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
                    "change_id": "coding-rollback-finalized",
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
    return build_hive_promotion_preview(
        _review(),
        category="coding",
        scope="queen",
    )


def _capabilities() -> dict:
    return {
        "schema_version": 1,
        "host": "hive",
        "litellm": {
            "installed_version": "1.83.4",
            "versions_match": True,
        },
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
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        },
    )


def _lifecycle(preview: dict) -> dict:
    gate = build_hive_promotion_gate(
        preview,
        _capabilities(),
        _evidence(),
    )
    return build_hive_promotion_lifecycle(
        _review(),
        preview,
        gate,
        _applied_receipt(preview),
    )


def _rollback_receipt(preview: dict) -> dict:
    return build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            }
        },
        previous_receipt=_applied_receipt(preview),
    )


def _rollback_plan() -> dict:
    status = {
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
                "active_change_id": "coding-rollback-finalized",
                "rollback_status": "verified",
                "rollback_target": _config("gpt-5.6-sol", "medium"),
                "manual_rollback_ready": True,
                "pending_promotions": [],
                "differences": [],
            }
        ],
        "blockers": [],
    }
    return build_hive_promotion_rollback_plan(status)


def _preflight(plan: dict) -> dict:
    return build_hive_promotion_rollback_preflight(
        plan,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        },
    )


def _artifacts() -> tuple[dict, dict, dict, dict]:
    preview = _preview()
    lifecycle = _lifecycle(preview)
    rollback_receipt = _rollback_receipt(preview)
    rollback_audit = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        rollback_receipt,
    )
    plan = _rollback_plan()
    finalization = build_hive_promotion_rollback_finalization(
        preview,
        lifecycle,
        plan,
        _preflight(plan),
        rollback_receipt,
    )
    return preview, lifecycle, rollback_audit, finalization


def _finalized_journal() -> tuple[dict, dict]:
    preview, lifecycle, rollback_audit, finalization = _artifacts()
    journal = build_hive_promotion_journal(preview)
    journal = append_hive_promotion_journal(
        journal,
        event="applied_lifecycle",
        artifact=lifecycle,
    )
    journal = append_hive_promotion_journal(
        journal,
        event="rollback_audit",
        artifact=rollback_audit,
    )
    journal = append_hive_promotion_journal(
        journal,
        event="rollback_finalization",
        artifact=finalization,
    )
    return journal, finalization


def test_finalization_carries_reviewed_promotion_identity() -> None:
    preview, _lifecycle_artifact, _rollback_audit, finalization = _artifacts()

    assert finalization["category"] == preview["category"]
    assert finalization["change_id"] == preview["change_id"]
    assert finalization["scope"] == preview["scope"]
    assert finalization["transition"] == preview["selected_transition"]
    assert finalization["rollback_verified_from_fresh_preflight"] is True


def test_finalized_rollback_journal_survives_checkpoint_registry_and_status() -> None:
    journal, finalization = _finalized_journal()

    assert journal["state"] == "rolled_back_finalized"
    assert [entry["event"] for entry in journal["entries"]] == [
        "promotion_preview",
        "applied_lifecycle",
        "rollback_audit",
        "rollback_finalization",
    ]
    assert (
        journal["entries"][-1]["evidence_hashes"]["rollback_audit_sha256"]
        == finalization["artifact_hashes"]["rollback_audit_sha256"]
        == journal["entries"][-2]["artifact_sha256"]
    )

    checkpoint = build_hive_promotion_checkpoint(journal)
    registry = build_hive_promotion_registry([(journal, checkpoint)])
    route = registry["routes"][0]

    assert registry["status"] == "ready"
    assert route["status"] == "current_verified"
    assert route["current_verified_config"]["model_id"] == "gpt-5.6-sol"
    assert route["active_change_id"] is None
    assert route["rollback_status"] == "not_available"
    assert route["evidence"][0]["state"] == "rolled_back_finalized"

    status = build_hive_promotion_status(
        [(journal, checkpoint)],
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            }
        },
    )
    assert status["status"] == "verified"
    assert status["operator_action"] == "none"
    assert status["manual_rollback_ready_count"] == 0


def test_rollback_finalization_requires_journaled_rollback_audit() -> None:
    preview, lifecycle, _rollback_audit, finalization = _artifacts()
    journal = append_hive_promotion_journal(
        build_hive_promotion_journal(preview),
        event="applied_lifecycle",
        artifact=lifecycle,
    )

    with pytest.raises(
        HivePromotionJournalError,
        match="can only follow a rolled_back_verified journal state",
    ):
        append_hive_promotion_journal(
            journal,
            event="rollback_finalization",
            artifact=finalization,
        )


def test_rollback_finalization_rejects_mismatched_rollback_audit_hash() -> None:
    preview, lifecycle, rollback_audit, finalization = _artifacts()
    journal = build_hive_promotion_journal(preview)
    journal = append_hive_promotion_journal(
        journal,
        event="applied_lifecycle",
        artifact=lifecycle,
    )
    journal = append_hive_promotion_journal(
        journal,
        event="rollback_audit",
        artifact=rollback_audit,
    )
    tampered = deepcopy(finalization)
    tampered["artifact_hashes"]["rollback_audit_sha256"] = "0" * 64

    with pytest.raises(
        HivePromotionJournalError,
        match="rollback_audit_sha256 does not match",
    ):
        append_hive_promotion_journal(
            journal,
            event="rollback_finalization",
            artifact=tampered,
        )
