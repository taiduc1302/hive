from __future__ import annotations

from copy import deepcopy

from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_lifecycle import (
    _canonical_sha256,
    build_hive_promotion_lifecycle,
)
from tools.ai_model_advisor.hive_promotion_preview import (
    build_hive_promotion_preview,
)
from tools.ai_model_advisor.hive_promotion_receipt import (
    build_hive_promotion_receipt,
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
                    "change_id": "coding-test-change",
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


def _preview(review: dict) -> dict:
    return build_hive_promotion_preview(
        review,
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
        "transport": {
            "single_call_evidence_ready": True,
        },
        "native_config": {
            "reasoning_effort_passthrough": True,
        },
    }


def _evidence() -> dict:
    return {
        "schema_version": 1,
        "transport": "hive_litellm",
        "applied_configuration": _config("gpt-6-astra", "high"),
        "litellm_version": "1.83.4",
        "outcome": "partial",
    }


def _gate(preview: dict) -> dict:
    return build_hive_promotion_gate(
        preview,
        _capabilities(),
        _evidence(),
    )


def test_preview_records_promotion_review_hash() -> None:
    review = _review()
    preview = _preview(review)

    assert preview["promotion_review_sha256"] == _canonical_sha256(review)


def test_lifecycle_waits_for_runtime_gate() -> None:
    review = _review()
    preview = _preview(review)

    report = build_hive_promotion_lifecycle(review, preview)

    assert report["state"] == "awaiting_runtime_gate"
    assert report["ready_for_manual_hive_edit"] is False
    assert report["applied_verified"] is False


def test_lifecycle_becomes_ready_after_runtime_gate() -> None:
    review = _review()
    preview = _preview(review)
    gate = _gate(preview)

    report = build_hive_promotion_lifecycle(review, preview, gate)

    assert gate["state"] == "runtime_ready_for_manual_hive_edit"
    assert report["state"] == "ready_for_manual_hive_edit"
    assert report["ready_for_manual_hive_edit"] is True
    assert report["applied_verified"] is False


def test_lifecycle_verifies_exact_manual_application() -> None:
    review = _review()
    preview = _preview(review)
    gate = _gate(preview)
    receipt = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        },
    )

    report = build_hive_promotion_lifecycle(
        review,
        preview,
        gate,
        receipt,
    )

    assert receipt["state"] == "applied_exactly"
    assert report["state"] == "applied_verified"
    assert report["applied_verified"] is True
    assert report["ready_for_manual_hive_edit"] is False


def test_lifecycle_keeps_ready_state_when_receipt_proves_not_applied() -> None:
    review = _review()
    preview = _preview(review)
    gate = _gate(preview)
    receipt = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            }
        },
    )

    report = build_hive_promotion_lifecycle(
        review,
        preview,
        gate,
        receipt,
    )

    assert receipt["state"] == "not_applied"
    assert report["state"] == "ready_for_manual_hive_edit"
    assert report["ready_for_manual_hive_edit"] is True


def test_lifecycle_blocks_tampered_review_after_preview_creation() -> None:
    review = _review()
    preview = _preview(review)
    tampered = deepcopy(review)
    tampered["reviews"][0]["manual_change"]["after"]["model_id"] = "gpt-tampered"

    report = build_hive_promotion_lifecycle(tampered, preview)

    assert report["state"] == "blocked_chain_mismatch"
    assert report["checks"][-1]["name"] == "review_to_preview_hash"


def test_lifecycle_blocks_gate_from_different_preview() -> None:
    review = _review()
    preview = _preview(review)
    gate = _gate(preview)
    gate["preview_sha256"] = "0" * 64

    report = build_hive_promotion_lifecycle(review, preview, gate)

    assert report["state"] == "blocked_chain_mismatch"
    assert "preview_sha256" in report["reason"]


def test_lifecycle_surfaces_runtime_gate_blocker() -> None:
    review = _review()
    preview = _preview(review)
    gate = build_hive_promotion_gate(
        preview,
        {
            "schema_version": 1,
            "host": "hive",
            "litellm": {
                "installed_version": "1.83.4",
                "versions_match": True,
            },
            "transport": {
                "single_call_evidence_ready": False,
            },
            "native_config": {
                "reasoning_effort_passthrough": True,
            },
        },
        None,
    )

    report = build_hive_promotion_lifecycle(review, preview, gate)

    assert gate["ready"] is False
    assert report["state"] == "blocked_runtime_gate"
    assert report["ready_for_manual_hive_edit"] is False


def test_lifecycle_blocks_post_application_drift() -> None:
    review = _review()
    preview = _preview(review)
    gate = _gate(preview)
    receipt = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": "openai",
                "model": "unexpected-model",
                "reasoning_effort": "low",
            }
        },
    )

    report = build_hive_promotion_lifecycle(
        review,
        preview,
        gate,
        receipt,
    )

    assert receipt["state"] == "drifted"
    assert report["state"] == "blocked_post_apply_drift"
    assert report["applied_verified"] is False
