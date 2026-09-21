from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_CONFIG_KEYS = ("provider", "model_id", "effort", "execution_mode")


class HivePromotionLifecycleError(ValueError):
    """Raised when promotion lifecycle artifacts are malformed."""


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
        raise HivePromotionLifecycleError(f"{label} must contain non-empty {key}")
    return value.strip()


def _config(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HivePromotionLifecycleError(f"{label} must be an object")
    return {
        key: _required_string(value, key, label=label)
        for key in _CONFIG_KEYS
    }


def _select_review(report: dict[str, Any], category: str) -> dict[str, Any]:
    reviews = report.get("reviews")
    if not isinstance(reviews, list):
        raise HivePromotionLifecycleError("promotion review must contain a reviews list")
    matches = [
        item
        for item in reviews
        if isinstance(item, dict)
        and str(item.get("category", "")).strip() == category
    ]
    if not matches:
        raise HivePromotionLifecycleError(
            f"promotion review has no category {category!r}"
        )
    if len(matches) > 1:
        raise HivePromotionLifecycleError(
            f"promotion review contains duplicate category {category!r}"
        )
    return matches[0]


def _review_transition(
    report: dict[str, Any],
    *,
    category: str,
) -> tuple[str, dict[str, dict[str, str]]]:
    if report.get("safe_to_apply") is True:
        raise HivePromotionLifecycleError("promotion review must keep safe_to_apply=false")
    if report.get("automatic_policy_mutation") is True:
        raise HivePromotionLifecycleError(
            "promotion review must keep automatic_policy_mutation=false"
        )
    if report.get("automatic_rollback") is True:
        raise HivePromotionLifecycleError(
            "promotion review must keep automatic_rollback=false"
        )

    item = _select_review(report, category)
    if item.get("state") != "ready_for_manual_edit":
        raise HivePromotionLifecycleError(
            "selected promotion review is not ready_for_manual_edit"
        )
    if item.get("safe_to_apply") is not False:
        raise HivePromotionLifecycleError(
            "selected promotion review must keep safe_to_apply=false"
        )
    if item.get("requires_human_approval") is not True:
        raise HivePromotionLifecycleError(
            "selected promotion review must require human approval"
        )

    change = item.get("manual_change")
    if not isinstance(change, dict):
        raise HivePromotionLifecycleError(
            "selected promotion review must contain manual_change"
        )
    change_id = _required_string(change, "change_id", label="manual_change")
    transition = {
        "before": _config(change.get("before"), label="manual_change.before"),
        "after": _config(change.get("after"), label="manual_change.after"),
        "rollback_to": _config(
            change.get("rollback_to"),
            label="manual_change.rollback_to",
        ),
    }
    if transition["rollback_to"] != transition["before"]:
        raise HivePromotionLifecycleError(
            "manual_change.rollback_to must exactly match manual_change.before"
        )
    return change_id, transition


def _preview_transition(
    preview: dict[str, Any],
) -> tuple[str, str, str, dict[str, dict[str, str]]]:
    if preview.get("schema_version") != 1:
        raise HivePromotionLifecycleError("promotion preview schema_version must be 1")
    if preview.get("host") != "hive":
        raise HivePromotionLifecycleError("promotion preview host must be hive")
    if preview.get("safe_to_auto_apply") is not False:
        raise HivePromotionLifecycleError(
            "promotion preview must keep safe_to_auto_apply=false"
        )
    if preview.get("automatic_config_mutation") is not False:
        raise HivePromotionLifecycleError(
            "promotion preview must keep automatic_config_mutation=false"
        )
    if preview.get("requires_human_approval") is not True:
        raise HivePromotionLifecycleError(
            "promotion preview must require human approval"
        )

    category = _required_string(preview, "category", label="promotion preview")
    change_id = _required_string(preview, "change_id", label="promotion preview")
    scope = _required_string(preview, "scope", label="promotion preview")
    if scope not in {"queen", "worker", "both"}:
        raise HivePromotionLifecycleError(
            "promotion preview scope must be queen, worker, or both"
        )

    raw = preview.get("selected_transition")
    if not isinstance(raw, dict):
        raise HivePromotionLifecycleError(
            "promotion preview must contain selected_transition"
        )
    transition = {
        "before": _config(raw.get("before"), label="selected_transition.before"),
        "after": _config(raw.get("after"), label="selected_transition.after"),
        "rollback_to": _config(
            raw.get("rollback_to"),
            label="selected_transition.rollback_to",
        ),
    }
    if transition["rollback_to"] != transition["before"]:
        raise HivePromotionLifecycleError(
            "selected_transition.rollback_to must exactly match before"
        )
    return category, change_id, scope, transition


def _check_record(
    checks: list[dict[str, str]],
    name: str,
    status: str,
    detail: str,
) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _blocked(
    base: dict[str, Any],
    checks: list[dict[str, str]],
    state: str,
    reason: str,
    *,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **base,
        "state": state,
        "reason": reason,
        "ready_for_manual_hive_edit": False,
        "applied_verified": False,
        "checks": checks,
        "blockers": blockers or [reason],
    }


def _validate_gate_link(
    gate: dict[str, Any],
    *,
    preview_sha: str,
    category: str,
    change_id: str,
    scope: str,
    after: dict[str, str],
) -> None:
    if gate.get("schema_version") != 1 or gate.get("host") != "hive":
        raise HivePromotionLifecycleError(
            "runtime gate must be Hive schema_version 1"
        )
    if gate.get("safe_to_auto_apply") is not False:
        raise HivePromotionLifecycleError(
            "runtime gate must keep safe_to_auto_apply=false"
        )
    if gate.get("automatic_config_mutation") is not False:
        raise HivePromotionLifecycleError(
            "runtime gate must keep automatic_config_mutation=false"
        )
    if gate.get("requires_human_approval") is not True:
        raise HivePromotionLifecycleError(
            "runtime gate must require human approval"
        )
    if gate.get("preview_sha256") != preview_sha:
        raise HivePromotionLifecycleError(
            "runtime gate preview_sha256 does not match the supplied promotion preview"
        )
    if gate.get("category") != category:
        raise HivePromotionLifecycleError(
            "runtime gate category does not match the promotion preview"
        )
    if gate.get("change_id") != change_id:
        raise HivePromotionLifecycleError(
            "runtime gate change_id does not match the promotion preview"
        )
    if gate.get("scope") != scope:
        raise HivePromotionLifecycleError(
            "runtime gate scope does not match the promotion preview"
        )
    if gate.get("after_configuration") != after:
        raise HivePromotionLifecycleError(
            "runtime gate after_configuration does not match the reviewed target"
        )


def _validate_receipt_link(
    receipt: dict[str, Any],
    *,
    preview_sha: str,
    category: str,
    change_id: str,
    scope: str,
    after: dict[str, str],
) -> None:
    if receipt.get("schema_version") != 1 or receipt.get("host") != "hive":
        raise HivePromotionLifecycleError(
            "promotion receipt must be Hive schema_version 1"
        )
    if receipt.get("safe_to_auto_mutate") is not False:
        raise HivePromotionLifecycleError(
            "promotion receipt must keep safe_to_auto_mutate=false"
        )
    if receipt.get("automatic_config_mutation") is not False:
        raise HivePromotionLifecycleError(
            "promotion receipt must keep automatic_config_mutation=false"
        )
    if receipt.get("requires_human_review") is not True:
        raise HivePromotionLifecycleError(
            "promotion receipt must require human review"
        )
    if receipt.get("preview_sha256") != preview_sha:
        raise HivePromotionLifecycleError(
            "promotion receipt preview_sha256 does not match the supplied preview"
        )
    if receipt.get("category") != category:
        raise HivePromotionLifecycleError(
            "promotion receipt category does not match the promotion preview"
        )
    if receipt.get("verified_change_id") != change_id:
        raise HivePromotionLifecycleError(
            "promotion receipt change_id does not match the promotion preview"
        )
    if receipt.get("scope") != scope:
        raise HivePromotionLifecycleError(
            "promotion receipt scope does not match the promotion preview"
        )
    if receipt.get("expected_after") != after:
        raise HivePromotionLifecycleError(
            "promotion receipt expected_after does not match the reviewed target"
        )


def build_hive_promotion_lifecycle(
    review: dict[str, Any],
    preview: dict[str, Any],
    runtime_gate: dict[str, Any] | None = None,
    promotion_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit the read-only evidence chain for one reviewed Hive promotion."""
    if not isinstance(review, dict):
        raise HivePromotionLifecycleError("promotion review must be a JSON object")
    if not isinstance(preview, dict):
        raise HivePromotionLifecycleError("promotion preview must be a JSON object")
    if runtime_gate is not None and not isinstance(runtime_gate, dict):
        raise HivePromotionLifecycleError("runtime gate must be a JSON object")
    if promotion_receipt is not None and not isinstance(promotion_receipt, dict):
        raise HivePromotionLifecycleError("promotion receipt must be a JSON object")

    review_sha = _canonical_sha256(review)
    preview_sha = _canonical_sha256(preview)
    checks: list[dict[str, str]] = []

    base: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "artifact_hashes": {
            "promotion_review_sha256": review_sha,
            "promotion_preview_sha256": preview_sha,
            "runtime_gate_sha256": (
                _canonical_sha256(runtime_gate)
                if isinstance(runtime_gate, dict)
                else None
            ),
            "promotion_receipt_sha256": (
                _canonical_sha256(promotion_receipt)
                if isinstance(promotion_receipt, dict)
                else None
            ),
        },
    }

    try:
        category, change_id, scope, preview_transition = _preview_transition(preview)
    except HivePromotionLifecycleError as exc:
        _check_record(checks, "promotion_preview", "fail", str(exc))
        return _blocked(
            base,
            checks,
            "blocked_invalid_preview",
            str(exc),
        )

    base.update(
        {
            "category": category,
            "change_id": change_id,
            "scope": scope,
            "transition": preview_transition,
        }
    )

    try:
        review_change_id, review_transition = _review_transition(
            review,
            category=category,
        )
    except HivePromotionLifecycleError as exc:
        _check_record(checks, "promotion_review", "fail", str(exc))
        return _blocked(
            base,
            checks,
            "blocked_invalid_review",
            str(exc),
        )

    expected_review_sha = preview.get("promotion_review_sha256")
    if expected_review_sha != review_sha:
        detail = (
            "promotion preview is not cryptographically linked to the supplied "
            "promotion review"
        )
        _check_record(checks, "review_to_preview_hash", "fail", detail)
        return _blocked(base, checks, "blocked_chain_mismatch", detail)
    _check_record(
        checks,
        "review_to_preview_hash",
        "pass",
        "promotion review SHA-256 matches preview provenance",
    )

    if review_change_id != change_id or review_transition != preview_transition:
        detail = "promotion review transition does not exactly match the promotion preview"
        _check_record(checks, "review_to_preview_transition", "fail", detail)
        return _blocked(base, checks, "blocked_chain_mismatch", detail)
    _check_record(
        checks,
        "review_to_preview_transition",
        "pass",
        "change_id and before/after/rollback transition match exactly",
    )

    if preview.get("state") != "ready_for_manual_hive_edit":
        detail = "promotion preview is not ready_for_manual_hive_edit"
        _check_record(checks, "promotion_preview_state", "fail", detail)
        return _blocked(base, checks, "blocked_preview", detail)
    _check_record(
        checks,
        "promotion_preview_state",
        "pass",
        "promotion preview is ready for the runtime gate",
    )

    if runtime_gate is None:
        return {
            **base,
            "state": "awaiting_runtime_gate",
            "reason": (
                "Review and preview are linked. Current runtime proof is still required "
                "before any manual Hive edit."
            ),
            "ready_for_manual_hive_edit": False,
            "applied_verified": False,
            "checks": checks,
            "blockers": ["runtime gate artifact is not supplied"],
        }

    try:
        _validate_gate_link(
            runtime_gate,
            preview_sha=preview_sha,
            category=category,
            change_id=change_id,
            scope=scope,
            after=preview_transition["after"],
        )
    except HivePromotionLifecycleError as exc:
        _check_record(checks, "preview_to_runtime_gate", "fail", str(exc))
        return _blocked(base, checks, "blocked_chain_mismatch", str(exc))
    _check_record(
        checks,
        "preview_to_runtime_gate",
        "pass",
        "runtime gate is linked to the exact promotion preview",
    )

    if (
        runtime_gate.get("state") != "runtime_ready_for_manual_hive_edit"
        or runtime_gate.get("ready") is not True
    ):
        detail = (
            "runtime gate has not proven the reviewed transition ready for a manual Hive edit"
        )
        _check_record(checks, "runtime_gate_state", "fail", detail)
        blockers = runtime_gate.get("blockers")
        return _blocked(
            base,
            checks,
            "blocked_runtime_gate",
            detail,
            blockers=blockers if isinstance(blockers, list) and blockers else None,
        )
    _check_record(
        checks,
        "runtime_gate_state",
        "pass",
        "runtime plumbing and exact candidate wire evidence are proven",
    )

    if promotion_receipt is None:
        return {
            **base,
            "state": "ready_for_manual_hive_edit",
            "reason": (
                "The reviewed transition and current runtime evidence are linked and ready. "
                "The Hive config edit remains a human action."
            ),
            "ready_for_manual_hive_edit": True,
            "applied_verified": False,
            "checks": checks,
            "blockers": [],
        }

    try:
        _validate_receipt_link(
            promotion_receipt,
            preview_sha=preview_sha,
            category=category,
            change_id=change_id,
            scope=scope,
            after=preview_transition["after"],
        )
    except HivePromotionLifecycleError as exc:
        _check_record(checks, "preview_to_receipt", "fail", str(exc))
        return _blocked(base, checks, "blocked_chain_mismatch", str(exc))
    _check_record(
        checks,
        "preview_to_receipt",
        "pass",
        "post-application receipt is linked to the exact promotion preview",
    )

    receipt_state = promotion_receipt.get("state")
    if receipt_state == "applied_exactly":
        _check_record(
            checks,
            "post_application_state",
            "pass",
            "all reviewed Hive sections exactly match the approved after state",
        )
        return {
            **base,
            "state": "applied_verified",
            "reason": (
                "The full review -> preview -> runtime gate -> receipt chain is linked, "
                "and the manually applied Hive configuration matches the approved target."
            ),
            "ready_for_manual_hive_edit": False,
            "applied_verified": True,
            "checks": checks,
            "blockers": [],
        }

    if receipt_state == "not_applied":
        _check_record(
            checks,
            "post_application_state",
            "pending",
            "Hive configuration still matches the reviewed before state",
        )
        return {
            **base,
            "state": "ready_for_manual_hive_edit",
            "reason": (
                "Runtime evidence is ready, but the receipt proves the reviewed Hive edit "
                "has not yet been applied."
            ),
            "ready_for_manual_hive_edit": True,
            "applied_verified": False,
            "checks": checks,
            "blockers": [],
        }

    if receipt_state == "drifted":
        detail = (
            "post-application receipt reports configuration drift from both the approved "
            "before and after states"
        )
        _check_record(checks, "post_application_state", "fail", detail)
        return _blocked(
            base,
            checks,
            "blocked_post_apply_drift",
            detail,
        )

    detail = f"unsupported promotion receipt state: {receipt_state!r}"
    _check_record(checks, "post_application_state", "fail", detail)
    return _blocked(base, checks, "blocked_receipt_state", detail)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Lifecycle Audit",
        "",
        f"- State: **{report['state']}**",
        f"- Category: **{report.get('category') or '—'}**",
        f"- Scope: **{report.get('scope') or '—'}**",
        f"- Change ID: `{report.get('change_id') or '—'}`",
        f"- Ready for manual Hive edit: **{'yes' if report.get('ready_for_manual_hive_edit') else 'no'}**",
        f"- Applied and verified: **{'yes' if report.get('applied_verified') else 'no'}**",
        "- Automatic config mutation: **disabled**",
        "",
        report["reason"],
        "",
        "## Evidence chain",
        "",
    ]
    for check in report.get("checks") or []:
        lines.append(
            f"- **{check['status']}** `{check['name']}`: {check['detail']}"
        )

    hashes = report.get("artifact_hashes") or {}
    lines.extend(["", "## Artifact hashes", ""])
    for key, value in hashes.items():
        lines.append(f"- {key}: `{value or '—'}`")

    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)

    lines.extend(
        [
            "",
            "This audit is read-only. It never edits Hive configuration or routing policy.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionLifecycleError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the cryptographic evidence chain for a reviewed Hive promotion."
        )
    )
    parser.add_argument("--promotion-review", required=True)
    parser.add_argument("--promotion-preview", required=True)
    parser.add_argument("--runtime-gate")
    parser.add_argument("--promotion-receipt")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    parser.add_argument(
        "--require-applied-verified",
        action="store_true",
        help="Exit 2 unless the lifecycle reaches applied_verified.",
    )
    args = parser.parse_args(argv)

    report = build_hive_promotion_lifecycle(
        _load_json_object(args.promotion_review, "promotion review"),
        _load_json_object(args.promotion_preview, "promotion preview"),
        (
            _load_json_object(args.runtime_gate, "runtime gate")
            if args.runtime_gate
            else None
        ),
        (
            _load_json_object(args.promotion_receipt, "promotion receipt")
            if args.promotion_receipt
            else None
        ),
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

    if args.require_applied_verified and not report.get("applied_verified"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
