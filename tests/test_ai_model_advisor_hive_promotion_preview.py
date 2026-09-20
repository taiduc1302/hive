from __future__ import annotations

import pytest

from tools.ai_model_advisor.hive_promotion_preview import (
    HivePromotionPreviewError,
    build_hive_promotion_preview,
)


def _config(
    *,
    provider: str = "openai",
    model_id: str = "gpt-6-astra",
    effort: str = "high",
    execution_mode: str = "single",
) -> dict:
    return {
        "provider": provider,
        "model_id": model_id,
        "effort": effort,
        "execution_mode": execution_mode,
    }


def _report(
    *,
    before: dict | None = None,
    after: dict | None = None,
    rollback: dict | None = None,
    state: str = "ready_for_manual_edit",
) -> dict:
    before = before or _config(model_id="gpt-5.6-sol")
    after = after or _config(model_id="gpt-6-astra", effort="xhigh")
    rollback = rollback or dict(before)
    return {
        "safe_to_apply": False,
        "automatic_policy_mutation": False,
        "reviews": [
            {
                "category": "debugging",
                "state": state,
                "safe_to_apply": False,
                "requires_human_approval": state == "ready_for_manual_edit",
                "manual_change": {
                    "change_id": "debugging-abc123",
                    "before": before,
                    "after": after,
                    "rollback_to": rollback,
                }
                if state == "ready_for_manual_edit"
                else None,
            }
        ],
    }


def test_ready_review_builds_apply_and_rollback_patch() -> None:
    preview = build_hive_promotion_preview(
        _report(),
        category="debugging",
        scope="queen",
    )

    assert preview["state"] == "ready_for_manual_hive_edit"
    assert preview["apply_patch"] == {
        "llm": {
            "model": "gpt-6-astra",
            "reasoning_effort": "xhigh",
        }
    }
    assert preview["rollback_patch"] == {
        "llm": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
        }
    }
    assert preview["safe_to_auto_apply"] is False
    assert preview["automatic_config_mutation"] is False


def test_default_effort_uses_null_override_removal() -> None:
    preview = build_hive_promotion_preview(
        _report(after=_config(model_id="gpt-6-astra", effort="default")),
        category="debugging",
    )

    assert preview["apply_patch"]["llm"]["reasoning_effort"] is None


def test_both_scope_builds_explicit_queen_and_worker_patches() -> None:
    preview = build_hive_promotion_preview(
        _report(),
        category="debugging",
        scope="both",
    )

    assert preview["apply_patch"]["llm"] == preview["apply_patch"]["worker_llm"]
    assert preview["preconditions"]["sections_to_verify"] == ["llm", "worker_llm"]


def test_cross_provider_change_fails_closed() -> None:
    preview = build_hive_promotion_preview(
        _report(after=_config(provider="anthropic", model_id="claude-opus-5")),
        category="debugging",
    )

    assert preview["state"] == "blocked_provider_change"
    assert preview["apply_patch"] is None
    assert preview["rollback_patch"] is None


def test_non_single_execution_change_fails_closed() -> None:
    preview = build_hive_promotion_preview(
        _report(after=_config(execution_mode="subagents")),
        category="debugging",
    )

    assert preview["state"] == "blocked_execution_mode"
    assert preview["apply_patch"] is None


def test_inconsistent_rollback_fails_closed() -> None:
    preview = build_hive_promotion_preview(
        _report(rollback=_config(model_id="unexpected")),
        category="debugging",
    )

    assert preview["state"] == "blocked_inconsistent_review"
    assert preview["apply_patch"] is None


def test_not_ready_review_never_produces_patch() -> None:
    preview = build_hive_promotion_preview(
        _report(state="blocked_route_drift"),
        category="debugging",
    )

    assert preview["state"] == "blocked_not_ready"
    assert preview["apply_patch"] is None


def test_missing_category_is_rejected() -> None:
    with pytest.raises(HivePromotionPreviewError, match="no category"):
        build_hive_promotion_preview(
            _report(),
            category="research",
        )


def test_central_cli_exposes_hive_promotion_preview() -> None:
    args = build_parser().parse_args(
        [
            "hive-promotion-preview",
            "--promotion-review",
            "review.json",
            "--category",
            "debugging",
        ]
    )

    assert args.func.__name__ == "command_hive_promotion_preview"
    assert args.scope == "queen"
