from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .hive_promotion_rollback import build_hive_promotion_rollback_audit


class HivePromotionRollbackFinalizeError(ValueError):
    """Raised when rollback finalization evidence is malformed or inconsistent."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_plan(plan: dict[str, Any]) -> str:
    supplied = plan.get("rollback_plan_sha256")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise HivePromotionRollbackFinalizeError(
            "rollback plan must contain rollback_plan_sha256"
        )
    material = deepcopy(plan)
    material.pop("rollback_plan_sha256", None)
    if _canonical_sha256(material) != supplied:
        raise HivePromotionRollbackFinalizeError(
            "rollback plan rollback_plan_sha256 is invalid"
        )
    return supplied


def _validate_preflight(preflight: dict[str, Any], *, plan_sha: str) -> str:
    if preflight.get("schema_version") != 1:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight schema_version must be 1"
        )
    if preflight.get("host") != "hive":
        raise HivePromotionRollbackFinalizeError("rollback preflight host must be hive")
    if preflight.get("state") != "ready_for_manual_edit":
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must be ready_for_manual_edit"
        )
    if preflight.get("ready_for_manual_edit") is not True:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must set ready_for_manual_edit=true"
        )
    if preflight.get("safe_to_auto_apply") is not False:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must keep safe_to_auto_apply=false"
        )
    if preflight.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must keep automatic_config_mutation=false"
        )
    if preflight.get("automatic_rollback") is not False:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must keep automatic_rollback=false"
        )
    if preflight.get("requires_human_approval") is not True:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must require human approval"
        )
    if preflight.get("rollback_plan_sha256") != plan_sha:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight does not match supplied rollback plan"
        )

    supplied = preflight.get("rollback_preflight_sha256")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight must contain rollback_preflight_sha256"
        )
    material = deepcopy(preflight)
    material.pop("rollback_preflight_sha256", None)
    if _canonical_sha256(material) != supplied:
        raise HivePromotionRollbackFinalizeError(
            "rollback preflight rollback_preflight_sha256 is invalid"
        )
    return supplied


def build_hive_promotion_rollback_finalization(
    promotion_preview: dict[str, Any],
    applied_lifecycle: dict[str, Any],
    rollback_plan: dict[str, Any],
    rollback_preflight: dict[str, Any],
    rollback_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Bind a verified manual rollback to the exact fresh preflight used before editing."""
    inputs = {
        "promotion preview": promotion_preview,
        "applied lifecycle": applied_lifecycle,
        "rollback plan": rollback_plan,
        "rollback preflight": rollback_preflight,
        "rollback receipt": rollback_receipt,
    }
    for label, value in inputs.items():
        if not isinstance(value, dict):
            raise HivePromotionRollbackFinalizeError(f"{label} must be a JSON object")

    plan_sha = _validate_plan(rollback_plan)
    preflight_sha = _validate_preflight(rollback_preflight, plan_sha=plan_sha)
    audit = build_hive_promotion_rollback_audit(
        promotion_preview,
        applied_lifecycle,
        rollback_receipt,
    )

    artifact_hashes = {
        "promotion_preview_sha256": _canonical_sha256(promotion_preview),
        "applied_lifecycle_sha256": _canonical_sha256(applied_lifecycle),
        "rollback_plan_sha256": plan_sha,
        "rollback_preflight_sha256": preflight_sha,
        "rollback_receipt_sha256": _canonical_sha256(rollback_receipt),
        "rollback_audit_sha256": _canonical_sha256(audit),
    }

    if not audit.get("rolled_back_verified"):
        report = {
            "schema_version": 1,
            "host": "hive",
            "state": "blocked_rollback_audit",
            "rollback_verified_from_fresh_preflight": False,
            "safe_to_auto_apply": False,
            "automatic_config_mutation": False,
            "automatic_rollback": False,
            "requires_human_approval": True,
            "reason": audit.get("reason") or "rollback audit did not verify",
            "rollback_audit_state": audit.get("state"),
            "artifact_hashes": artifact_hashes,
            "blockers": audit.get("blockers") or ["rollback audit did not verify"],
        }
    else:
        report = {
            "schema_version": 1,
            "host": "hive",
            "state": "rollback_verified_from_fresh_preflight",
            "rollback_verified_from_fresh_preflight": True,
            "safe_to_auto_apply": False,
            "automatic_config_mutation": False,
            "automatic_rollback": False,
            "requires_human_approval": True,
            "reason": (
                "The supplied rollback plan and successful freshness preflight are "
                "self-consistent, and the existing rollback audit verifies the final "
                "Hive config returned exactly to the reviewed rollback target."
            ),
            "rollback_audit_state": audit.get("state"),
            "artifact_hashes": artifact_hashes,
            "blockers": [],
        }

    report["rollback_finalization_sha256"] = _canonical_sha256(report)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Rollback Finalization",
        "",
        f"- State: **{report['state']}**",
        (
            "- Verified from fresh preflight: "
            f"**{'yes' if report.get('rollback_verified_from_fresh_preflight') else 'no'}**"
        ),
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        report["reason"],
        "",
        "## Evidence chain",
        "",
    ]
    for key, value in report["artifact_hashes"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append(
        f"- rollback_finalization_sha256: `{report['rollback_finalization_sha256']}`"
    )
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
    lines.extend(
        [
            "",
            "This artifact is verification-only. It does not edit Hive configuration.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionRollbackFinalizeError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bind a verified manual Hive rollback to its fresh rollback preflight."
    )
    parser.add_argument("--promotion-preview", required=True)
    parser.add_argument("--applied-lifecycle", required=True)
    parser.add_argument("--rollback-plan", required=True)
    parser.add_argument("--rollback-preflight", required=True)
    parser.add_argument("--rollback-receipt", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args(argv)

    report = build_hive_promotion_rollback_finalization(
        _load_json_object(args.promotion_preview, "promotion preview"),
        _load_json_object(args.applied_lifecycle, "applied lifecycle"),
        _load_json_object(args.rollback_plan, "rollback plan"),
        _load_json_object(args.rollback_preflight, "rollback preflight"),
        _load_json_object(args.rollback_receipt, "rollback receipt"),
    )
    markdown = render_markdown(report)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if args.json_output:
        target = Path(args.json_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.require_verified and not report["rollback_verified_from_fresh_preflight"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
