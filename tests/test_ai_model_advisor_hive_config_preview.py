from __future__ import annotations

import pytest

from tools.ai_model_advisor.hive_config_preview import (
    HiveConfigPreviewError,
    build_hive_config_preview,
    render_markdown,
)


def _payload(effort: str = "high", mode: str = "single") -> dict:
    return {
        "recommendations": [
            {
                "provider": "openai",
                "model_id": "gpt-6-astra",
                "effort": effort,
                "execution_mode": mode,
            }
        ]
    }


def test_preview_builds_queen_effort_merge_patch() -> None:
    preview = build_hive_config_preview(_payload(), scope="queen")

    assert preview["merge_patch"] == {"llm": {"reasoning_effort": "high"}}
    assert preview["safe_to_auto_apply"] is False
    assert preview["warnings"] == []


def test_preview_builds_worker_and_both_scopes() -> None:
    worker = build_hive_config_preview(_payload("medium"), scope="worker")
    both = build_hive_config_preview(_payload("xhigh"), scope="both")

    assert worker["merge_patch"] == {"worker_llm": {"reasoning_effort": "medium"}}
    assert both["merge_patch"] == {
        "llm": {"reasoning_effort": "xhigh"},
        "worker_llm": {"reasoning_effort": "xhigh"},
    }


def test_default_effort_uses_null_to_remove_explicit_override() -> None:
    preview = build_hive_config_preview(_payload("default"), scope="queen")

    assert preview["merge_patch"] == {"llm": {"reasoning_effort": None}}
    assert "null removes" in preview["merge_patch_semantics"]


def test_preview_warns_when_orchestration_is_not_applied() -> None:
    preview = build_hive_config_preview(_payload("high", "subagents"), scope="queen")

    assert len(preview["warnings"]) == 1
    assert "not applied by this patch" in preview["warnings"][0]


def test_preview_rejects_missing_recommendations_and_bad_index() -> None:
    with pytest.raises(HiveConfigPreviewError, match="non-empty recommendations"):
        build_hive_config_preview({})
    with pytest.raises(HiveConfigPreviewError, match="out of range"):
        build_hive_config_preview(_payload(), index=4)


def test_markdown_makes_non_mutating_boundary_explicit() -> None:
    markdown = render_markdown(build_hive_config_preview(_payload(), scope="queen"))

    assert "Auto-apply: **disabled**" in markdown
    assert '"reasoning_effort": "high"' in markdown
    assert "does not change credentials, provider, model, or orchestration" in markdown
