from __future__ import annotations

import json
from argparse import Namespace
from copy import deepcopy

import pytest

from tools.ai_model_advisor.cli import command_hive_promotion_journal
from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate
from tools.ai_model_advisor.hive_promotion_journal import (
    HivePromotionJournalError,
    append_hive_promotion_journal,
    build_hive_promotion_journal,
    validate_hive_promotion_journal,
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
                    "change_id": "coding-journal-test",
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


def _rollback_audit(preview: dict, lifecycle: dict) -> dict:
    rollback_receipt = build_hive_promotion_receipt(
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
    return build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        rollback_receipt,
    )


def test_journal_tracks_preview_apply_and_rollback_chain() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    rollback = _rollback_audit(preview, lifecycle)

    journal = build_hive_promotion_journal(preview)
    journal = append_hive_promotion_journal(
        journal,
        event="applied_lifecycle",
        artifact=lifecycle,
    )
    journal = append_hive_promotion_journal(
        journal,
        event="rollback_audit",
        artifact=rollback,
    )

    validate_hive_promotion_journal(journal)
    assert journal["state"] == "rolled_back_verified"
    assert [item["event"] for item in journal["entries"]] == [
        "promotion_preview",
        "applied_lifecycle",
        "rollback_audit",
    ]
    assert journal["entries"][1]["previous_entry_sha256"] == (
        journal["entries"][0]["entry_sha256"]
    )
    assert journal["entries"][2]["previous_entry_sha256"] == (
        journal["entries"][1]["entry_sha256"]
    )
    assert journal["automatic_config_mutation"] is False
    assert journal["automatic_rollback"] is False


def test_journal_rejects_tampered_prior_entry() -> None:
    journal = build_hive_promotion_journal(_preview())
    tampered = deepcopy(journal)
    tampered["entries"][0]["artifact_sha256"] = "0" * 64

    with pytest.raises(HivePromotionJournalError, match="entry_sha256"):
        validate_hive_promotion_journal(tampered)


def test_journal_rejects_rollback_before_application() -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    rollback = _rollback_audit(preview, lifecycle)
    journal = build_hive_promotion_journal(preview)

    with pytest.raises(
        HivePromotionJournalError,
        match="rollback_audit can only follow an applied_verified",
    ):
        append_hive_promotion_journal(
            journal,
            event="rollback_audit",
            artifact=rollback,
        )


def test_journal_rejects_applied_lifecycle_from_different_preview() -> None:
    preview = _preview()
    lifecycle = deepcopy(_applied_lifecycle(preview))
    lifecycle["artifact_hashes"]["promotion_preview_sha256"] = "1" * 64

    with pytest.raises(
        HivePromotionJournalError,
        match="promotion_preview_sha256",
    ):
        append_hive_promotion_journal(
            build_hive_promotion_journal(preview),
            event="applied_lifecycle",
            artifact=lifecycle,
        )


def test_unified_cli_builds_and_appends_promotion_journal(tmp_path) -> None:
    preview = _preview()
    lifecycle = _applied_lifecycle(preview)
    rollback = _rollback_audit(preview, lifecycle)

    preview_path = tmp_path / "preview.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    rollback_path = tmp_path / "rollback.json"
    journal_path = tmp_path / "journal.json"
    applied_path = tmp_path / "journal-applied.json"
    final_path = tmp_path / "journal-final.json"
    markdown_path = tmp_path / "journal-final.md"

    for path, payload in (
        (preview_path, preview),
        (lifecycle_path, lifecycle),
        (rollback_path, rollback),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")

    assert command_hive_promotion_journal(
        Namespace(
            journal_action="init",
            promotion_preview=str(preview_path),
            output=None,
            json_output=str(journal_path),
        )
    ) == 0

    assert command_hive_promotion_journal(
        Namespace(
            journal_action="append",
            journal=str(journal_path),
            event="applied_lifecycle",
            artifact=str(lifecycle_path),
            output=None,
            json_output=str(applied_path),
        )
    ) == 0

    assert command_hive_promotion_journal(
        Namespace(
            journal_action="append",
            journal=str(applied_path),
            event="rollback_audit",
            artifact=str(rollback_path),
            output=str(markdown_path),
            json_output=str(final_path),
        )
    ) == 0

    final = json.loads(final_path.read_text(encoding="utf-8"))
    assert final["state"] == "rolled_back_verified"
    assert final["head_entry_sha256"]
    assert "Hive Promotion Journal" in markdown_path.read_text(encoding="utf-8")
