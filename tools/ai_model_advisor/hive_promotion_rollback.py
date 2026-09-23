from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_CONFIG_KEYS = ("provider", "model_id", "effort", "execution_mode")


class HivePromotionRollbackError(ValueError):
    """Raised when rollback-audit inputs are malformed."""


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
        raise HivePromotionRollbackError(f"{label} must contain non-empty {key}")
    return value.strip()


def _config(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HivePromotionRollbackError(f"{label} must be an object")
    return {
        key: _required_string(value, key, label=label)
        for key in _CONFIG_KEYS
    }


def _preview_transition(
    preview: dict[str, Any],
) -> tuple[str, str, str, dict[str, dict[str, str]]]:
    if preview.get("schema_version") != 1:
        raise HivePromotionRollbackError("promotion preview schema_version must be 1")
    if preview.get("host") != "hive":
        raise HivePromotionRollbackError("promotion preview host must be hive")
    if preview.get("state") != "ready_for_manual_hive_edit":
        raise HivePromotionRollbackError(
            "promotion preview must be ready_for_manual_hive_edit"
        )
    if preview.get("safe_to_auto_apply") is not False:
        raise HivePromotionRollbackError(
            "promotion preview must keep safe_to_auto_apply=false"
        )
    if preview.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackError(
            "promotion preview must keep automatic_config_mutation=false"
        )
    if preview.get("requires_human_approval") is not True:
        raise HivePromotionRollbackError(
            "promotion preview must require human approval"
        )

    category = _required_string(preview, "category", label="promotion preview")
    change_id = _required_string(preview, "change_id", label="promotion preview")
    scope = _required_string(preview, "scope", label="promotion preview")
    if scope not in {"queen", "worker", "both"}:
        raise HivePromotionRollbackError(
            "promotion preview scope must be queen, worker, or both"
        )

    raw_transition = preview.get("selected_transition")
    if not isinstance(raw_transition, dict):
        raise HivePromotionRollbackError(
            "promotion preview must contain selected_transition"
        )
    transition = {
        "before": _config(
            raw_transition.get("before"),
            label="selected_transition.before",
        ),
        "after": _config(
            raw_transition.get("after"),
            label="selected_transition.after",
        ),
        "rollback_to": _config(
            raw_transition.get("rollback_to"),
            label="selected_transition.rollback_to",
        ),
    }
    if transition["rollback_to"] != transition["before"]:
        raise HivePromotionRollbackError(
            "selected_transition.rollback_to must exactly match before"
        )
    return category, change_id, scope, transition


def _blocked(
    base: dict[str, Any],
    state: str,
    reason: str,
    *,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **base,
        "state": state,
        "reason": reason,
        "rolled_back_verified": False,
        "blockers": blockers or [reason],
    }


def _validate_applied_lifecycle(
    lifecycle: dict[str, Any],
    *,
    preview_sha: str,
    category: str,
    change_id: str,
    scope: str,
    transition: dict[str, dict[str, str]],
) -> str:
    if lifecycle.get("schema_version") != 1:
        raise HivePromotionRollbackError(
            "applied lifecycle schema_version must be 1"
        )
    if lifecycle.get("host") != "hive":
        raise HivePromotionRollbackError("applied lifecycle host must be hive")
    if lifecycle.get("state") != "applied_verified":
        raise HivePromotionRollbackError(
            "applied lifecycle must have state=applied_verified"
        )
    if lifecycle.get("applied_verified") is not True:
        raise HivePromotionRollbackError(
            "applied lifecycle must set applied_verified=true"
        )
    if lifecycle.get("ready_for_manual_hive_edit") is not False:
        raise HivePromotionRollbackError(
            "applied lifecycle must no longer be ready_for_manual_hive_edit"
        )
    if lifecycle.get("safe_to_auto_apply") is not False:
        raise HivePromotionRollbackError(
            "applied lifecycle must keep safe_to_auto_apply=false"
        )
    if lifecycle.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackError(
            "applied lifecycle must keep automatic_config_mutation=false"
        )
    if lifecycle.get("automatic_rollback") is not False:
        raise HivePromotionRollbackError(
            "applied lifecycle must keep automatic_rollback=false"
        )
    if lifecycle.get("requires_human_approval") is not True:
        raise HivePromotionRollbackError(
            "applied lifecycle must require human approval"
        )
    if lifecycle.get("category") != category:
        raise HivePromotionRollbackError(
            "applied lifecycle category does not match promotion preview"
        )
    if lifecycle.get("change_id") != change_id:
        raise HivePromotionRollbackError(
            "applied lifecycle change_id does not match promotion preview"
        )
    if lifecycle.get("scope") != scope:
        raise HivePromotionRollbackError(
            "applied lifecycle scope does not match promotion preview"
        )
    if lifecycle.get("transition") != transition:
        raise HivePromotionRollbackError(
            "applied lifecycle transition does not match promotion preview"
        )

    hashes = lifecycle.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise HivePromotionRollbackError(
            "applied lifecycle must contain artifact_hashes"
        )
    if hashes.get("promotion_preview_sha256") != preview_sha:
        raise HivePromotionRollbackError(
            "applied lifecycle promotion_preview_sha256 does not match supplied preview"
        )
    applied_receipt_sha = hashes.get("promotion_receipt_sha256")
    if not isinstance(applied_receipt_sha, str) or not applied_receipt_sha.strip():
        raise HivePromotionRollbackError(
            "applied lifecycle must identify the applied promotion receipt"
        )
    return applied_receipt_sha.strip()


def _validate_rollback_receipt(
    receipt: dict[str, Any],
    *,
    preview_sha: str,
    category: str,
    change_id: str,
    scope: str,
    transition: dict[str, dict[str, str]],
    applied_receipt_sha: str,
) -> None:
    if receipt.get("schema_version") != 1:
        raise HivePromotionRollbackError(
            "rollback receipt schema_version must be 1"
        )
    if receipt.get("host") != "hive":
        raise HivePromotionRollbackError("rollback receipt host must be hive")
    if receipt.get("safe_to_auto_mutate") is not False:
        raise HivePromotionRollbackError(
            "rollback receipt must keep safe_to_auto_mutate=false"
        )
    if receipt.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackError(
            "rollback receipt must keep automatic_config_mutation=false"
        )
    if receipt.get("requires_human_review") is not True:
        raise HivePromotionRollbackError(
            "rollback receipt must require human review"
        )
    if receipt.get("preview_sha256") != preview_sha:
        raise HivePromotionRollbackError(
            "rollback receipt preview_sha256 does not match supplied preview"
        )
    if receipt.get("category") != category:
        raise HivePromotionRollbackError(
            "rollback receipt category does not match promotion preview"
        )
    if receipt.get("verified_change_id") != change_id:
        raise HivePromotionRollbackError(
            "rollback receipt change_id does not match promotion preview"
        )
    if receipt.get("scope") != scope:
        raise HivePromotionRollbackError(
            "rollback receipt scope does not match promotion preview"
        )
    if receipt.get("expected_before") != transition["before"]:
        raise HivePromotionRollbackError(
            "rollback receipt expected_before does not match reviewed before state"
        )
    if receipt.get("expected_after") != transition["after"]:
        raise HivePromotionRollbackError(
            "rollback receipt expected_after does not match reviewed after state"
        )
    if receipt.get("previous_receipt_sha256") != applied_receipt_sha:
        raise HivePromotionRollbackError(
            "rollback receipt previous_receipt_sha256 does not match the "
            "applied receipt recorded by the verified lifecycle"
        )


def build_hive_promotion_rollback_audit(
    preview: dict[str, Any],
    applied_lifecycle: dict[str, Any],
    rollback_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Verify that a previously applied Hive promotion was manually rolled back."""
    if not isinstance(preview, dict):
        raise HivePromotionRollbackError("promotion preview must be a JSON object")
    if not isinstance(applied_lifecycle, dict):
        raise HivePromotionRollbackError(
            "applied lifecycle must be a JSON object"
        )
    if not isinstance(rollback_receipt, dict):
        raise HivePromotionRollbackError(
            "rollback receipt must be a JSON object"
        )

    preview_sha = _canonical_sha256(preview)
    lifecycle_sha = _canonical_sha256(applied_lifecycle)
    receipt_sha = _canonical_sha256(rollback_receipt)

    base: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "artifact_hashes": {
            "promotion_preview_sha256": preview_sha,
            "applied_lifecycle_sha256": lifecycle_sha,
            "rollback_receipt_sha256": receipt_sha,
        },
    }

    try:
        category, change_id, scope, transition = _preview_transition(preview)
    except HivePromotionRollbackError as exc:
        return _blocked(
            base,
            "blocked_invalid_preview",
            str(exc),
        )

    base.update(
        {
            "category": category,
            "change_id": change_id,
            "scope": scope,
            "transition": transition,
        }
    )

    try:
        applied_receipt_sha = _validate_applied_lifecycle(
            applied_lifecycle,
            preview_sha=preview_sha,
            category=category,
            change_id=change_id,
            scope=scope,
            transition=transition,
            applied_receipt_sha=applied_receipt_sha,
        )
    except HivePromotionRollbackError as exc:
        state = (
            "blocked_invalid_applied_lifecycle"
            if "state=applied_verified" in str(exc)
            or "applied_verified=true" in str(exc)
            else "blocked_chain_mismatch"
        )
        return _blocked(base, state, str(exc))

    try:
        _validate_rollback_receipt(
            rollback_receipt,
            preview_sha=preview_sha,
            category=category,
            change_id=change_id,
            scope=scope,
            transition=transition,
        )
    except HivePromotionRollbackError as exc:
        return _blocked(
            base,
            "blocked_chain_mismatch",
            str(exc),
        )

    receipt_state = rollback_receipt.get("state")
    if receipt_state == "not_applied":
        return {
            **base,
            "state": "rolled_back_verified",
            "reason": (
                "A prior applied_verified lifecycle proves the promotion was applied, "
                "and the new receipt proves every reviewed Hive section has returned "
                "exactly to the approved before/rollback state."
            ),
            "rolled_back_verified": True,
            "blockers": [],
        }
    if receipt_state == "applied_exactly":
        return _blocked(
            base,
            "rollback_not_applied",
            (
                "The new receipt still matches the approved after state. "
                "The reviewed rollback has not been applied."
            ),
        )
    if receipt_state == "drifted":
        return _blocked(
            base,
            "blocked_rollback_drift",
            (
                "The new receipt matches neither the complete reviewed before state "
                "nor the complete reviewed after state."
            ),
        )
    return _blocked(
        base,
        "blocked_rollback_receipt_state",
        f"unsupported rollback receipt state: {receipt_state!r}",
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Rollback Audit",
        "",
        f"- State: **{report['state']}**",
        f"- Category: **{report.get('category') or '—'}**",
        f"- Scope: **{report.get('scope') or '—'}**",
        f"- Change ID: `{report.get('change_id') or '—'}`",
        f"- Rollback verified: **{'yes' if report.get('rolled_back_verified') else 'no'}**",
        "- Automatic rollback: **disabled**",
        "- Automatic config mutation: **disabled**",
        "",
        report["reason"],
        "",
        "## Artifact hashes",
        "",
    ]
    for key, value in (report.get("artifact_hashes") or {}).items():
        lines.append(f"- {key}: `{value or '—'}`")

    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)

    lines.extend(
        [
            "",
            (
                "This audit is verification-only. It proves a manual rollback "
                "after a previously verified application; it never edits Hive configuration."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionRollbackError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that a previously applied Hive promotion was manually "
            "rolled back to its reviewed before state."
        )
    )
    parser.add_argument("--promotion-preview", required=True)
    parser.add_argument("--applied-lifecycle", required=True)
    parser.add_argument("--rollback-receipt", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    parser.add_argument(
        "--require-rolled-back-verified",
        action="store_true",
        help="Exit 2 unless the rollback is cryptographically linked and verified.",
    )
    args = parser.parse_args(argv)

    report = build_hive_promotion_rollback_audit(
        _load_json_object(args.promotion_preview, "promotion preview"),
        _load_json_object(args.applied_lifecycle, "applied lifecycle"),
        _load_json_object(args.rollback_receipt, "rollback receipt"),
    )
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json
        else render_markdown(report)
    )
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    if args.require_rolled_back_verified and not report.get(
        "rolled_back_verified"
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
