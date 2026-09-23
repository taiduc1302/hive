from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .hive_promotion_journal import (
    HivePromotionJournalError,
    validate_hive_promotion_journal,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class HivePromotionCheckpointError(ValueError):
    """Raised when a Hive promotion checkpoint is malformed."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_string(mapping: dict[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HivePromotionCheckpointError(f"{label} must contain non-empty {key}")
    return value.strip()


def _required_sha256(mapping: dict[str, Any], key: str, *, label: str) -> str:
    value = _required_string(mapping, key, label=label)
    if not _SHA256_RE.fullmatch(value):
        raise HivePromotionCheckpointError(
            f"{label} {key} must be a lowercase 64-character SHA-256 hex digest"
        )
    return value


def _checkpoint_payload(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": checkpoint["schema_version"],
        "host": checkpoint["host"],
        "category": checkpoint["category"],
        "change_id": checkpoint["change_id"],
        "scope": checkpoint["scope"],
        "state": checkpoint["state"],
        "entry_count": checkpoint["entry_count"],
        "head_entry_sha256": checkpoint["head_entry_sha256"],
        "journal_sha256": checkpoint["journal_sha256"],
        "transition_sha256": checkpoint["transition_sha256"],
        "safe_to_auto_apply": checkpoint["safe_to_auto_apply"],
        "automatic_config_mutation": checkpoint["automatic_config_mutation"],
        "automatic_rollback": checkpoint["automatic_rollback"],
        "requires_human_approval": checkpoint["requires_human_approval"],
    }


def validate_hive_promotion_checkpoint(checkpoint: dict[str, Any]) -> None:
    """Validate checkpoint structure and its self-hash."""
    if not isinstance(checkpoint, dict):
        raise HivePromotionCheckpointError("promotion checkpoint must be a JSON object")
    if checkpoint.get("schema_version") != 1:
        raise HivePromotionCheckpointError(
            "promotion checkpoint schema_version must be 1"
        )
    if checkpoint.get("host") != "hive":
        raise HivePromotionCheckpointError("promotion checkpoint host must be hive")
    if checkpoint.get("safe_to_auto_apply") is not False:
        raise HivePromotionCheckpointError(
            "promotion checkpoint must keep safe_to_auto_apply=false"
        )
    if checkpoint.get("automatic_config_mutation") is not False:
        raise HivePromotionCheckpointError(
            "promotion checkpoint must keep automatic_config_mutation=false"
        )
    if checkpoint.get("automatic_rollback") is not False:
        raise HivePromotionCheckpointError(
            "promotion checkpoint must keep automatic_rollback=false"
        )
    if checkpoint.get("requires_human_approval") is not True:
        raise HivePromotionCheckpointError(
            "promotion checkpoint must require human approval"
        )

    _required_string(checkpoint, "category", label="promotion checkpoint")
    _required_string(checkpoint, "change_id", label="promotion checkpoint")
    scope = _required_string(checkpoint, "scope", label="promotion checkpoint")
    if scope not in {"queen", "worker", "both"}:
        raise HivePromotionCheckpointError(
            "promotion checkpoint scope must be queen, worker, or both"
        )
    _required_string(checkpoint, "state", label="promotion checkpoint")

    entry_count = checkpoint.get("entry_count")
    if not isinstance(entry_count, int) or isinstance(entry_count, bool) or entry_count < 1:
        raise HivePromotionCheckpointError(
            "promotion checkpoint entry_count must be a positive integer"
        )

    _required_sha256(
        checkpoint,
        "head_entry_sha256",
        label="promotion checkpoint",
    )
    _required_sha256(checkpoint, "journal_sha256", label="promotion checkpoint")
    _required_sha256(
        checkpoint,
        "transition_sha256",
        label="promotion checkpoint",
    )
    supplied = _required_sha256(
        checkpoint,
        "checkpoint_sha256",
        label="promotion checkpoint",
    )
    expected = _canonical_sha256(_checkpoint_payload(checkpoint))
    if supplied != expected:
        raise HivePromotionCheckpointError(
            "promotion checkpoint checkpoint_sha256 is invalid"
        )


def build_hive_promotion_checkpoint(journal: dict[str, Any]) -> dict[str, Any]:
    """Create a portable commitment to the exact current promotion journal."""
    try:
        validate_hive_promotion_journal(journal)
    except HivePromotionJournalError as exc:
        raise HivePromotionCheckpointError(str(exc)) from exc

    checkpoint: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "category": journal["category"],
        "change_id": journal["change_id"],
        "scope": journal["scope"],
        "state": journal["state"],
        "entry_count": len(journal["entries"]),
        "head_entry_sha256": journal["head_entry_sha256"],
        "journal_sha256": _canonical_sha256(journal),
        "transition_sha256": _canonical_sha256(journal["transition"]),
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
    }
    checkpoint["checkpoint_sha256"] = _canonical_sha256(
        _checkpoint_payload(checkpoint)
    )
    validate_hive_promotion_checkpoint(checkpoint)
    return checkpoint


def verify_hive_promotion_checkpoint(
    journal: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Verify an exact journal snapshot against an independently stored checkpoint."""
    validate_hive_promotion_checkpoint(checkpoint)
    try:
        validate_hive_promotion_journal(journal)
    except HivePromotionJournalError as exc:
        raise HivePromotionCheckpointError(str(exc)) from exc

    current = {
        "category": journal["category"],
        "change_id": journal["change_id"],
        "scope": journal["scope"],
        "state": journal["state"],
        "entry_count": len(journal["entries"]),
        "head_entry_sha256": journal["head_entry_sha256"],
        "journal_sha256": _canonical_sha256(journal),
        "transition_sha256": _canonical_sha256(journal["transition"]),
    }
    expected = {
        key: checkpoint[key]
        for key in (
            "category",
            "change_id",
            "scope",
            "state",
            "entry_count",
            "head_entry_sha256",
            "journal_sha256",
            "transition_sha256",
        )
    }
    mismatches = [
        {
            "field": key,
            "expected": expected[key],
            "actual": current[key],
        }
        for key in expected
        if current[key] != expected[key]
    ]
    return {
        "schema_version": 1,
        "host": "hive",
        "status": "verified" if not mismatches else "mismatch",
        "verified": not mismatches,
        "category": journal["category"],
        "change_id": journal["change_id"],
        "scope": journal["scope"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "mismatches": mismatches,
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "evidence_boundary": (
            "A checkpoint detects rollback, truncation, replacement, or later journal "
            "advancement only when the checkpoint is retained independently from the "
            "journal being verified. It is a hash commitment, not a signature or "
            "trusted timestamp."
        ),
    }


def render_checkpoint_markdown(checkpoint: dict[str, Any]) -> str:
    validate_hive_promotion_checkpoint(checkpoint)
    return "\n".join(
        [
            "# Hive Promotion Journal Checkpoint",
            "",
            f"- Category: **{checkpoint['category']}**",
            f"- Scope: **{checkpoint['scope']}**",
            f"- Change ID: `{checkpoint['change_id']}`",
            f"- State: **{checkpoint['state']}**",
            f"- Journal entries: **{checkpoint['entry_count']}**",
            f"- Journal SHA-256: `{checkpoint['journal_sha256']}`",
            f"- Head entry SHA-256: `{checkpoint['head_entry_sha256']}`",
            f"- Checkpoint SHA-256: `{checkpoint['checkpoint_sha256']}`",
            "",
            "Store this checkpoint separately from the promotion journal if it is "
            "intended to detect later rollback or truncation of that journal.",
            "",
        ]
    )


def render_verification_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Journal Checkpoint Verification",
        "",
        f"- Status: **{report['status']}**",
        f"- Category: **{report['category']}**",
        f"- Scope: **{report['scope']}**",
        f"- Change ID: `{report['change_id']}`",
        f"- Checkpoint SHA-256: `{report['checkpoint_sha256']}`",
    ]
    if report["mismatches"]:
        lines.extend(["", "## Mismatches", ""])
        for mismatch in report["mismatches"]:
            lines.append(
                f"- `{mismatch['field']}`: expected `{mismatch['expected']}`, "
                f"got `{mismatch['actual']}`"
            )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            report["evidence_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionCheckpointError(f"{label} must be a JSON object")
    return payload


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or verify an independently stored checkpoint for a Hive "
            "promotion journal."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--journal", required=True)
    create.add_argument("--output")
    create.add_argument("--json-output")

    verify = sub.add_parser("verify")
    verify.add_argument("--journal", required=True)
    verify.add_argument("--checkpoint", required=True)
    verify.add_argument("--output")
    verify.add_argument("--json-output")

    args = parser.parse_args(argv)

    if args.command == "create":
        checkpoint = build_hive_promotion_checkpoint(
            _load_json_object(args.journal, "promotion journal")
        )
        markdown = render_checkpoint_markdown(checkpoint)
        payload: dict[str, Any] = checkpoint
        exit_code = 0
    else:
        report = verify_hive_promotion_checkpoint(
            _load_json_object(args.journal, "promotion journal"),
            _load_json_object(args.checkpoint, "promotion checkpoint"),
        )
        markdown = render_verification_markdown(report)
        payload = report
        exit_code = 0 if report["verified"] else 2

    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
