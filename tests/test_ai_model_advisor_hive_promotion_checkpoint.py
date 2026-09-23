from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tools.ai_model_advisor.hive_promotion_checkpoint import (
    HivePromotionCheckpointError,
    build_hive_promotion_checkpoint,
    validate_hive_promotion_checkpoint,
    verify_hive_promotion_checkpoint,
)
from tools.ai_model_advisor.hive_promotion_checkpoint import (
    main as checkpoint_main,
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
                    "change_id": "coding-checkpoint-test",
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


def _applied_lifecycle(preview: dict) -> dict:
    gate = build_hive_promotion_gate(
        preview,
        _capabilities(),
        _evidence(),
    )
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
    return build_hive_promotion_lifecycle(
        _review(),
        preview,
        gate,
        receipt,
    )


def _applied_journal() -> dict:
    preview = _preview()
    return append_hive_promotion_journal(
        build_hive_promotion_journal(preview),
        event="applied_lifecycle",
        artifact=_applied_lifecycle(preview),
    )


def test_checkpoint_round_trip_verifies_exact_journal() -> None:
    journal = _applied_journal()
    checkpoint = build_hive_promotion_checkpoint(journal)

    validate_hive_promotion_checkpoint(checkpoint)
    report = verify_hive_promotion_checkpoint(journal, checkpoint)

    assert report["verified"] is True
    assert report["status"] == "verified"
    assert report["mismatches"] == []
    assert checkpoint["entry_count"] == 2
    assert checkpoint["head_entry_sha256"] == journal["head_entry_sha256"]
    assert checkpoint["safe_to_auto_apply"] is False
    assert checkpoint["automatic_config_mutation"] is False
    assert checkpoint["automatic_rollback"] is False


def test_checkpoint_detects_rollback_to_older_valid_prefix() -> None:
    preview = _preview()
    older_prefix = build_hive_promotion_journal(preview)
    current = append_hive_promotion_journal(
        older_prefix,
        event="applied_lifecycle",
        artifact=_applied_lifecycle(preview),
    )
    checkpoint = build_hive_promotion_checkpoint(current)

    report = verify_hive_promotion_checkpoint(older_prefix, checkpoint)

    assert report["verified"] is False
    fields = {item["field"] for item in report["mismatches"]}
    assert {"state", "entry_count", "head_entry_sha256", "journal_sha256"} <= fields


def test_checkpoint_detects_journal_advancement_after_snapshot() -> None:
    preview = _preview()
    genesis = build_hive_promotion_journal(preview)
    checkpoint = build_hive_promotion_checkpoint(genesis)
    advanced = append_hive_promotion_journal(
        genesis,
        event="applied_lifecycle",
        artifact=_applied_lifecycle(preview),
    )

    report = verify_hive_promotion_checkpoint(advanced, checkpoint)

    assert report["verified"] is False
    assert any(item["field"] == "entry_count" for item in report["mismatches"])


def test_checkpoint_rejects_tampered_self_hash() -> None:
    checkpoint = build_hive_promotion_checkpoint(_applied_journal())
    tampered = deepcopy(checkpoint)
    tampered["state"] = "previewed"

    with pytest.raises(HivePromotionCheckpointError, match="checkpoint_sha256"):
        validate_hive_promotion_checkpoint(tampered)


def test_checkpoint_cli_returns_two_for_snapshot_mismatch(tmp_path) -> None:
    preview = _preview()
    genesis = build_hive_promotion_journal(preview)
    current = append_hive_promotion_journal(
        genesis,
        event="applied_lifecycle",
        artifact=_applied_lifecycle(preview),
    )
    current_path = tmp_path / "current.json"
    older_path = tmp_path / "older.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    report_path = tmp_path / "report.json"

    current_path.write_text(json.dumps(current), encoding="utf-8")
    older_path.write_text(json.dumps(genesis), encoding="utf-8")

    assert checkpoint_main(
        [
            "create",
            "--journal",
            str(current_path),
            "--json-output",
            str(checkpoint_path),
        ]
    ) == 0

    assert checkpoint_main(
        [
            "verify",
            "--journal",
            str(older_path),
            "--checkpoint",
            str(checkpoint_path),
            "--json-output",
            str(report_path),
        ]
    ) == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "mismatch"
    assert report["verified"] is False
