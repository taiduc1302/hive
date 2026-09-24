from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class HivePromotionRollbackPreflightError(ValueError):
    """Raised when a rollback plan or preflight input is malformed."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_section(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {
            "provider": None,
            "model": None,
            "reasoning_effort": "default",
            "reasoning_effort_key_present": False,
        }
    effort_present = "reasoning_effort" in section
    return {
        "provider": section.get("provider"),
        "model": section.get("model"),
        "reasoning_effort": (
            section.get("reasoning_effort") if effort_present else "default"
        ),
        "reasoning_effort_key_present": effort_present,
    }


def _validate_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("schema_version") != 1:
        raise HivePromotionRollbackPreflightError(
            "rollback plan schema_version must be 1"
        )
    if plan.get("host") != "hive":
        raise HivePromotionRollbackPreflightError("rollback plan host must be hive")
    if plan.get("safe_to_auto_apply") is not False:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must keep safe_to_auto_apply=false"
        )
    if plan.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must keep automatic_config_mutation=false"
        )
    if plan.get("automatic_rollback") is not False:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must keep automatic_rollback=false"
        )
    if plan.get("requires_human_approval") is not True:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must require human approval"
        )
    if plan.get("state") != "ready_for_manual_rollback":
        raise HivePromotionRollbackPreflightError(
            "rollback plan state must be ready_for_manual_rollback"
        )
    if plan.get("ready_for_manual_rollback") is not True:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must set ready_for_manual_rollback=true"
        )

    supplied_hash = plan.get("rollback_plan_sha256")
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must contain rollback_plan_sha256"
        )
    material = deepcopy(plan)
    material.pop("rollback_plan_sha256", None)
    expected_hash = _canonical_sha256(material)
    if supplied_hash != expected_hash:
        raise HivePromotionRollbackPreflightError(
            "rollback plan rollback_plan_sha256 is invalid"
        )

    preconditions = plan.get("preconditions")
    if not isinstance(preconditions, list) or not preconditions:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must contain preconditions"
        )
    merge_patch = plan.get("merge_patch")
    if not isinstance(merge_patch, dict) or not merge_patch:
        raise HivePromotionRollbackPreflightError(
            "rollback plan must contain a merge_patch"
        )
    return preconditions


def build_hive_promotion_rollback_preflight(
    plan: dict[str, Any],
    current_config: dict[str, Any],
) -> dict[str, Any]:
    """Re-check rollback-plan preconditions against the current Hive config."""
    if not isinstance(plan, dict):
        raise HivePromotionRollbackPreflightError(
            "rollback plan must be a JSON object"
        )
    if not isinstance(current_config, dict):
        raise HivePromotionRollbackPreflightError(
            "Hive configuration must be a JSON object"
        )

    preconditions = _validate_plan(plan)
    checks: list[dict[str, Any]] = []
    stale_count = 0

    for item in preconditions:
        if not isinstance(item, dict):
            raise HivePromotionRollbackPreflightError(
                "rollback plan precondition must be an object"
            )
        section = item.get("section")
        if section not in {"llm", "worker_llm"}:
            raise HivePromotionRollbackPreflightError(
                "rollback plan precondition has unsupported Hive section"
            )
        expected_sha = item.get("expected_current_sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise HivePromotionRollbackPreflightError(
                "rollback plan precondition must contain expected_current_sha256"
            )

        actual = _normalized_section(current_config.get(section))
        actual_sha = _canonical_sha256(actual)
        matches = actual_sha == expected_sha
        if not matches:
            stale_count += 1

        checks.append(
            {
                "section": section,
                "active_change_id": item.get("active_change_id"),
                "matches": matches,
                "expected_current_sha256": expected_sha,
                "actual_current_sha256": actual_sha,
                "actual": actual,
            }
        )

    ready = stale_count == 0
    report: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "state": (
            "ready_for_manual_edit"
            if ready
            else "blocked_stale_rollback_plan"
        ),
        "ready_for_manual_edit": ready,
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "rollback_plan_sha256": plan["rollback_plan_sha256"],
        "merge_patch": plan["merge_patch"] if ready else {},
        "precondition_checks": checks,
        "stale_precondition_count": stale_count,
        "privacy": (
            "Preflight emits only sanitized provider/model/reasoning-effort "
            "observations and hashes. Credentials, API bases, and unrelated Hive "
            "configuration are omitted."
        ),
        "evidence_boundary": (
            "A ready preflight proves only that the supplied Hive configuration "
            "matched the rollback plan preconditions at verification time. It does "
            "not mutate configuration and does not guarantee the config stays unchanged."
        ),
    }
    report["rollback_preflight_sha256"] = _canonical_sha256(report)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Manual Rollback Preflight",
        "",
        f"- State: **{report['state']}**",
        (
            "- Ready for manual edit: "
            f"**{'yes' if report.get('ready_for_manual_edit') else 'no'}**"
        ),
        f"- Stale preconditions: **{report['stale_precondition_count']}**",
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        "## Preconditions",
        "",
    ]

    for item in report["precondition_checks"]:
        lines.append(
            f"- {item['section']}: "
            f"{'match' if item['matches'] else 'STALE'} "
            f"(expected {item['expected_current_sha256']}, "
            f"actual {item['actual_current_sha256']})"
        )

    if report.get("ready_for_manual_edit"):
        lines.extend(
            [
                "",
                "## Merge patch preview",
                "",
                json.dumps(report["merge_patch"], ensure_ascii=False, indent=2),
            ]
        )

    lines.extend(
        [
            "",
            f"- Rollback plan SHA-256: {report['rollback_plan_sha256']}",
            f"- Preflight SHA-256: {report['rollback_preflight_sha256']}",
            "",
            report["privacy"],
            "",
            report["evidence_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionRollbackPreflightError(
            f"{label} must be a JSON object"
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a manual Hive rollback plan is still fresh against the "
            "current Hive configuration."
        )
    )
    parser.add_argument("--rollback-plan", required=True)
    parser.add_argument("--hive-config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 unless every rollback precondition still matches.",
    )
    args = parser.parse_args(argv)

    report = build_hive_promotion_rollback_preflight(
        _load_json_object(args.rollback_plan, "rollback plan"),
        _load_json_object(args.hive_config, "Hive configuration"),
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

    if args.require_ready and not report["ready_for_manual_edit"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
