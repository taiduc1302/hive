from __future__ import annotations

from copy import deepcopy

from tools.ai_model_advisor.hive_promotion_checkpoint import (
    build_hive_promotion_checkpoint,
)
from tools.ai_model_advisor.hive_promotion_journal import (
    append_hive_promotion_journal,
    build_hive_promotion_journal,
)
from tools.ai_model_advisor.hive_promotion_preview import (
    build_hive_promotion_preview,
)
from tools.ai_model_advisor.hive_promotion_registry import (
    build_hive_promotion_registry,
)
from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_lifecycle import (
    build_hive_promotion_lifecycle,
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


def _review(
    *,
    category: str = "coding",
    change_id: str = "coding-registry-test",
    before_model: str = "gpt-5.6-sol",
    after_model: str = "gpt-6-astra",
    before_effort: str = "medium",
    after_effort: str = "high",
) -> dict:
    before = _config(before_model, before_effort)
    after = _config(after_model, after_effort)
    return {
        "reviews": [
            {
                "category": category,
                "state": "ready_for_manual_edit",
                "safe_to_apply": False,
                "requires_human_approval": True,
                "manual_change": {
                    "change_id": change_id,
                    "category": category,
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


def _preview(review: dict, *, category: str = "coding", scope: str = "queen") -> dict:
    return build_hive_promotion_preview(
        review,
        category=category,
        scope=scope,
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


def _evidence(after: dict[str, str]) -> dict:
    return {
        "schema_version": 1,
        "transport": "hive_litellm",
        "litellm_version": "1.83.4",
        "applied_configuration": after,
        "outcome": "partial",
    }


def _applied_journal(
    review: dict,
    *,
    category: str = "coding",
    scope: str = "queen",
) -> dict:
    preview = _preview(review, category=category, scope=scope)
    after = preview["selected_transition"]["after"]
    gate = build_hive_promotion_gate(
        preview,
        _capabilities(),
        _evidence(after),
    )
    section_names = ["llm"] if scope == "queen" else ["worker_llm"]
    if scope == "both":
        section_names = ["llm", "worker_llm"]
    config = {
        section: {
            "provider": after["provider"],
            "model": after["model_id"],
            "reasoning_effort": after["effort"],
        }
        for section in section_names
    }
    receipt = build_hive_promotion_receipt(preview, config)
    lifecycle = build_hive_promotion_lifecycle(
        review,
        preview,
        gate,
        receipt,
    )
    journal = build_hive_promotion_journal(preview)
    return append_hive_promotion_journal(
        journal,
        event="applied_lifecycle",
        artifact=lifecycle,
    )


def _pair(journal: dict) -> tuple[dict, dict]:
    return journal, build_hive_promotion_checkpoint(journal)


def test_registry_reports_verified_active_config_and_rollback_target() -> None:
    journal = _applied_journal(_review())
    registry = build_hive_promotion_registry([_pair(journal)])

    assert registry["status"] == "ready"
    assert registry["checkpoint_verified_snapshot_count"] == 1
    assert len(registry["routes"]) == 1

    route = registry["routes"][0]
    assert route["category"] == "coding"
    assert route["route"] == "queen"
    assert route["status"] == "current_verified"
    assert route["current_verified_config"]["model_id"] == "gpt-6-astra"
    assert route["active_change_id"] == "coding-registry-test"
    assert route["rollback_status"] == "verified"
    assert route["rollback_target"]["model_id"] == "gpt-5.6-sol"


def test_registry_collapses_older_checkpointed_prefix_for_same_change() -> None:
    review = _review()
    preview = _preview(review)
    preview_journal = build_hive_promotion_journal(preview)
    applied_journal = _applied_journal(review)

    registry = build_hive_promotion_registry(
        [
            _pair(preview_journal),
            _pair(applied_journal),
        ]
    )

    assert registry["status"] == "ready"
    assert registry["snapshot_count"] == 2
    assert registry["selected_change_count"] == 1
    route = registry["routes"][0]
    assert route["status"] == "current_verified"
    assert route["pending_previews"] == []
    assert route["current_verified_config"]["model_id"] == "gpt-6-astra"


def test_registry_blocks_checkpoint_mismatch() -> None:
    journal = _applied_journal(_review())
    checkpoint = build_hive_promotion_checkpoint(journal)
    stale = deepcopy(journal)
    stale["state"] = "previewed"
    stale["entries"] = stale["entries"][:1]
    stale["head_entry_sha256"] = stale["entries"][0]["entry_sha256"]

    registry = build_hive_promotion_registry([(stale, checkpoint)])

    assert registry["status"] == "blocked"
    assert registry["checkpoint_verified_snapshot_count"] == 0
    assert registry["routes"] == []
    assert registry["blockers"][0]["code"] == "checkpoint_mismatch"


def test_registry_blocks_conflicting_current_state_for_same_route() -> None:
    first = _applied_journal(
        _review(
            change_id="coding-change-a",
            after_model="gpt-6-astra",
        )
    )
    second = _applied_journal(
        _review(
            change_id="coding-change-b",
            after_model="gpt-5.6-sol",
            after_effort="high",
        )
    )

    registry = build_hive_promotion_registry([_pair(first), _pair(second)])

    assert registry["status"] == "blocked"
    route = registry["routes"][0]
    assert route["status"] == "blocked_conflicting_current_state"
    assert route["current_verified_config"] is None
    assert any(
        blocker["code"] == "conflicting_current_state"
        for blocker in registry["blockers"]
    )


def test_registry_separates_categories_and_both_scope_routes() -> None:
    coding = _applied_journal(_review(), scope="both")
    research_review = _review(
        category="research",
        change_id="research-registry-test",
        after_model="gpt-6-astra",
        after_effort="medium",
    )
    research = _applied_journal(
        research_review,
        category="research",
        scope="worker",
    )

    registry = build_hive_promotion_registry(
        [_pair(coding), _pair(research)]
    )

    assert registry["status"] == "ready"
    keys = {(row["category"], row["route"]) for row in registry["routes"]}
    assert keys == {
        ("coding", "queen"),
        ("coding", "worker"),
        ("research", "worker"),
    }


def test_registry_marks_preview_as_pending_not_current_verified() -> None:
    review = _review(change_id="coding-preview-only")
    journal = build_hive_promotion_journal(_preview(review))

    registry = build_hive_promotion_registry([_pair(journal)])

    assert registry["status"] == "ready"
    route = registry["routes"][0]
    assert route["status"] == "pending_only"
    assert route["current_verified_config"] is None
    assert route["rollback_status"] == "not_available"
    assert route["pending_previews"][0]["change_id"] == "coding-preview-only"
