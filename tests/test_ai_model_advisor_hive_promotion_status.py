from __future__ import annotations

from copy import deepcopy

from tools.ai_model_advisor.hive_promotion_checkpoint import (
    build_hive_promotion_checkpoint,
)
from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_journal import (
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
from tools.ai_model_advisor.hive_promotion_status import (
    build_hive_promotion_status,
)


def _config(model_id: str, effort: str) -> dict[str, str]:
    return {
        "provider": "test",
        "model_id": model_id,
        "effort": effort,
        "execution_mode": "single",
    }


def _review(
    *,
    change_id: str = "status-test-change",
    before_model: str = "current-model",
    after_model: str = "candidate-model",
    before_effort: str = "medium",
    after_effort: str = "high",
) -> dict:
    before = _config(before_model, before_effort)
    after = _config(after_model, after_effort)
    return {
        "reviews": [
            {
                "category": "debugging",
                "state": "ready_for_manual_edit",
                "safe_to_apply": False,
                "requires_human_approval": True,
                "manual_change": {
                    "change_id": change_id,
                    "category": "debugging",
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
        category="debugging",
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


def _applied_journal(review: dict) -> dict:
    preview = _preview(review)
    after = preview["selected_transition"]["after"]
    evidence = {
        "schema_version": 1,
        "transport": "hive_litellm",
        "litellm_version": "1.83.4",
        "applied_configuration": after,
        "outcome": "partial",
    }
    gate = build_hive_promotion_gate(
        preview,
        _capabilities(),
        evidence,
    )
    receipt = build_hive_promotion_receipt(
        preview,
        {
            "llm": {
                "provider": after["provider"],
                "model": after["model_id"],
                "reasoning_effort": after["effort"],
            }
        },
    )
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


def test_status_bundle_reports_verified_operator_state() -> None:
    journal = _applied_journal(_review())
    report = build_hive_promotion_status(
        [_pair(journal)],
        {
            "llm": {
                "provider": "test",
                "model": "candidate-model",
                "reasoning_effort": "high",
                "api_key": "STATUS_SECRET_MUST_NOT_LEAK",
            }
        },
    )

    assert report["status"] == "verified"
    assert report["operator_action"] == "none"
    assert report["verified_route_count"] == 1
    assert report["drift_count"] == 0
    assert report["manual_rollback_ready_count"] == 1
    assert report["routes"][0]["manual_rollback_ready"] is True
    assert report["routes"][0]["rollback_target"]["model_id"] == "current-model"
    assert "STATUS_SECRET_MUST_NOT_LEAK" not in str(report)


def test_status_bundle_reports_pending_preview_attention() -> None:
    review = _review(change_id="pending-status-change")
    journal = build_hive_promotion_journal(_preview(review))

    report = build_hive_promotion_status(
        [_pair(journal)],
        {
            "llm": {
                "provider": "test",
                "model": "current-model",
                "reasoning_effort": "medium",
            }
        },
    )

    assert report["status"] == "attention"
    assert report["operator_action"] == "review_pending_promotions"
    assert report["pending_promotion_count"] == 1
    assert report["verified_route_count"] == 0
    assert report["routes"][0]["pending_promotions"][0]["change_id"] == (
        "pending-status-change"
    )


def test_status_bundle_reports_live_drift() -> None:
    journal = _applied_journal(_review())
    report = build_hive_promotion_status(
        [_pair(journal)],
        {
            "llm": {
                "provider": "test",
                "model": "unexpected-model",
                "reasoning_effort": "low",
            }
        },
    )

    assert report["status"] == "drifted"
    assert report["operator_action"] == "investigate_live_config_drift"
    assert report["drift_count"] == 1
    assert report["manual_rollback_ready_count"] == 0
    assert report["routes"][0]["live_state"] == "drifted"
    assert report["routes"][0]["differences"]


def test_status_bundle_blocks_checkpoint_mismatch() -> None:
    journal = _applied_journal(_review())
    checkpoint = build_hive_promotion_checkpoint(journal)
    stale = deepcopy(journal)
    stale["state"] = "previewed"
    stale["entries"] = stale["entries"][:1]
    stale["head_entry_sha256"] = stale["entries"][0]["entry_sha256"]

    report = build_hive_promotion_status(
        [(stale, checkpoint)],
        {},
    )

    assert report["status"] == "blocked"
    assert report["operator_action"] == "resolve_evidence_blockers"
    assert report["checkpoint_verified_snapshot_count"] == 0
    assert report["blockers"][0]["code"] == "checkpoint_mismatch"
    assert report["routes"] == []


def test_status_bundle_is_deterministic() -> None:
    journal = _applied_journal(_review())
    snapshots = [_pair(journal)]
    config = {
        "llm": {
            "provider": "test",
            "model": "candidate-model",
            "reasoning_effort": "high",
        }
    }

    first = build_hive_promotion_status(snapshots, config)
    second = build_hive_promotion_status(snapshots, config)

    assert first == second
    assert first["status_bundle_sha256"] == second["status_bundle_sha256"]
