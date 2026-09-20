from __future__ import annotations

from tools.ai_model_advisor.hive_promotion_receipt import (
    build_hive_promotion_receipt,
)


def _preview(scope: str = "queen") -> dict:
    sections = ["llm"] if scope == "queen" else ["llm", "worker_llm"]
    apply_patch = {
        section: {"model": "gpt-6-astra", "reasoning_effort": "high"}
        for section in sections
    }
    rollback_patch = {
        section: {"model": "gpt-5.6-sol", "reasoning_effort": "medium"}
        for section in sections
    }
    return {
        "schema_version": 1,
        "host": "hive",
        "category": "debugging",
        "scope": scope,
        "change_id": "debugging:gpt56-to-astra",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "requires_human_approval": True,
        "state": "ready_for_manual_hive_edit",
        "preconditions": {
            "expected_provider": "openai",
            "expected_current": {
                "provider": "openai",
                "model_id": "gpt-5.6-sol",
                "effort": "medium",
                "execution_mode": "single",
            },
            "sections_to_verify": sections,
            "operator_must_verify_current_config": True,
        },
        "selected_transition": {
            "before": {
                "provider": "openai",
                "model_id": "gpt-5.6-sol",
                "effort": "medium",
                "execution_mode": "single",
            },
            "after": {
                "provider": "openai",
                "model_id": "gpt-6-astra",
                "effort": "high",
                "execution_mode": "single",
            },
            "rollback_to": {
                "provider": "openai",
                "model_id": "gpt-5.6-sol",
                "effort": "medium",
                "execution_mode": "single",
            },
        },
        "apply_patch": apply_patch,
        "rollback_patch": rollback_patch,
    }


def test_receipt_reports_exact_application_without_leaking_secrets():
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
            "api_key": "TOP_SECRET_KEY",
            "api_base": "https://secret-proxy.example/v1",
        },
        "unrelated": {"token": "ANOTHER_SECRET"},
    }

    receipt = build_hive_promotion_receipt(_preview(), config)

    assert receipt["state"] == "applied_exactly"
    assert receipt["sections"][0]["state"] == "after"
    assert receipt["automatic_config_mutation"] is False
    serialized = str(receipt)
    assert "TOP_SECRET_KEY" not in serialized
    assert "ANOTHER_SECRET" not in serialized
    assert "secret-proxy" not in serialized
    assert receipt["receipt_sha256"]


def test_receipt_reports_not_applied_when_before_state_is_unchanged():
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        }
    }

    receipt = build_hive_promotion_receipt(_preview(), config)

    assert receipt["state"] == "not_applied"
    assert receipt["sections"][0]["state"] == "before"


def test_receipt_reports_drift_for_partial_both_scope_application():
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
        },
        "worker_llm": {
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        },
    }

    receipt = build_hive_promotion_receipt(_preview("both"), config)

    assert receipt["state"] == "drifted"
    assert [item["state"] for item in receipt["sections"]] == ["after", "before"]


def test_receipt_requires_default_effort_key_to_be_removed():
    preview = _preview()
    preview["selected_transition"]["after"]["effort"] = "default"
    preview["apply_patch"]["llm"]["reasoning_effort"] = None
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-6-astra",
            "reasoning_effort": None,
        }
    }

    receipt = build_hive_promotion_receipt(preview, config)

    assert receipt["state"] == "drifted"
    assert "should be absent" in receipt["sections"][0]["after_differences"][0]


def test_receipt_blocks_preview_that_is_not_review_only():
    preview = _preview()
    preview["safe_to_auto_apply"] = True

    receipt = build_hive_promotion_receipt(preview, {})

    assert receipt["state"] == "blocked_invalid_preview"
    assert receipt["sections"] == []
    assert receipt["automatic_config_mutation"] is False
