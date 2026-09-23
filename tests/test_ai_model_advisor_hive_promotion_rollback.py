from __future__ import annotations

import json
from argparse import Namespace
from copy import deepcopy

from tools.ai_model_advisor.cli import command_hive_promotion_rollback
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
from tools.ai_model_advisor.hive_promotion_rollback import (
    build_hive_promotion_rollback_audit,
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
                    "change_id": "coding-rollback-test",
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


def _applied_lifecycle(preview: dict) -> dict:
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


def test_rollback_audit_verifies_prior_application_then_exact_rollback() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    receipt = _rollback_receipt(preview)

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        receipt,
    )

    assert lifecycle["state"] == "applied_verified"
    assert receipt["state"] == "not_applied"
    assert report["state"] == "rolled_back_verified"
    assert report["rolled_back_verified"] is True
    assert report["automatic_rollback"] is False
    assert report["automatic_config_mutation"] is False


def test_rollback_audit_rejects_lifecycle_that_never_proved_application() -> None:
    preview = _preview()
    gate = build_hive_promotion_gate(
        preview,
        _capabilities(),
        _evidence(),
    )
    lifecycle = build_hive_promotion_lifecycle(
        _review(),
        preview,
        gate,
    )

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        _rollback_receipt(preview),
    )

    assert lifecycle["state"] == "ready_for_manual_hive_edit"
    assert report["state"] == "blocked_invalid_applied_lifecycle"
    assert report["rolled_back_verified"] is False


def test_rollback_audit_reports_rollback_not_applied() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    still_applied = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        },
        previous_receipt=_applied_receipt(preview),
    )

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        still_applied,
    )

    assert still_applied["state"] == "applied_exactly"
    assert report["state"] == "rollback_not_applied"
    assert report["rolled_back_verified"] is False


def test_rollback_audit_blocks_drifted_rollback() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    drifted = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "unexpected-model",
                "reasoning_effort": "low",
            }
        },
        previous_receipt=_applied_receipt(preview),
    )

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        drifted,
    )

    assert drifted["state"] == "drifted"
    assert report["state"] == "blocked_rollback_drift"


def test_rollback_audit_blocks_tampered_applied_lifecycle_link() -> None:
    preview = _preview()
    lifecycle = deepcopy(_applied_lifecycle(preview))
    lifecycle["artifact_hashes"]["promotion_preview_sha256"] = "0" * 64

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        _rollback_receipt(preview),
    )

    assert report["state"] == "blocked_chain_mismatch"
    assert "promotion_preview_sha256" in report["reason"]


def test_rollback_audit_blocks_replayed_pre_application_receipt() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    stale_receipt = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            }
        },
    )

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        stale_receipt,
    )

    assert stale_receipt["state"] == "not_applied"
    assert stale_receipt["previous_receipt_sha256"] is None
    assert report["state"] == "blocked_chain_mismatch"
    assert "previous_receipt_sha256" in report["reason"]


def test_rollback_audit_blocks_receipt_from_different_change() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    receipt = deepcopy(_rollback_receipt(preview))
    receipt["verified_change_id"] = "different-change"

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        receipt,
    )

    assert report["state"] == "blocked_chain_mismatch"
    assert "change_id" in report["reason"]


def test_unified_cli_command_writes_rolled_back_verified_artifacts(tmp_path) -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    receipt = _rollback_receipt(preview)

    preview_path = tmp_path / "preview.json"
    lifecycle_path = tmp_path / "applied-lifecycle.json"
    receipt_path = tmp_path / "rollback-receipt.json"
    markdown_path = tmp_path / "rollback.md"
    json_path = tmp_path / "rollback.json"

    for path, payload in (
        (preview_path, preview),
        (lifecycle_path, lifecycle),
        (receipt_path, receipt),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")

    code = command_hive_promotion_rollback(
        Namespace(
            promotion_preview=str(preview_path),
            applied_lifecycle=str(lifecycle_path),
            rollback_receipt=str(receipt_path),
            output=str(markdown_path),
            json_output=str(json_path),
            require_rolled_back_verified=True,
        )
    )

    assert code == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["state"] == "rolled_back_verified"
    assert payload["rolled_back_verified"] is True
    assert "Hive Promotion Rollback Audit" in markdown_path.read_text(
        encoding="utf-8"
    )
