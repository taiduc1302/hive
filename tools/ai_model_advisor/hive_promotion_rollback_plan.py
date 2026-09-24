from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_ROUTE_SECTION = {
    "queen": "llm",
    "worker": "worker_llm",
}


class HivePromotionRollbackPlanError(ValueError):
    """Raised when operational status cannot produce a safe rollback plan."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rollback_patch(config: dict[str, Any]) -> dict[str, Any]:
    effort = config.get("effort")
    return {
        "provider": config.get("provider"),
        "model": config.get("model_id"),
        "reasoning_effort": None if effort == "default" else effort,
    }


def _validate_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    if status.get("schema_version") != 1:
        raise HivePromotionRollbackPlanError(
            "promotion status schema_version must be 1"
        )
    if status.get("host") != "hive":
        raise HivePromotionRollbackPlanError("promotion status host must be hive")
    if status.get("safe_to_auto_apply") is not False:
        raise HivePromotionRollbackPlanError(
            "promotion status must keep safe_to_auto_apply=false"
        )
    if status.get("automatic_config_mutation") is not False:
        raise HivePromotionRollbackPlanError(
            "promotion status must keep automatic_config_mutation=false"
        )
    if status.get("automatic_rollback") is not False:
        raise HivePromotionRollbackPlanError(
            "promotion status must keep automatic_rollback=false"
        )
    if status.get("requires_human_approval") is not True:
        raise HivePromotionRollbackPlanError(
            "promotion status must require human approval"
        )

    routes = status.get("routes")
    if not isinstance(routes, list):
        raise HivePromotionRollbackPlanError(
            "promotion status routes must be a list"
        )
    return routes


def build_hive_promotion_rollback_plan(
    status: dict[str, Any],
) -> dict[str, Any]:
    """Create a non-mutating rollback patch preview from verified operator status."""
    if not isinstance(status, dict):
        raise HivePromotionRollbackPlanError(
            "promotion status must be a JSON object"
        )

    routes = _validate_status(status)
    status_sha = _canonical_sha256(status)

    base: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "status_bundle_sha256": status_sha,
        "state": "blocked",
        "ready_for_manual_rollback": False,
        "merge_patch": {},
        "operations": [],
        "blockers": [],
        "preconditions": [],
        "evidence_boundary": (
            "This artifact is a rollback preview only. It is generated from one "
            "operational status bundle and does not mutate Hive configuration. "
            "The operator must re-verify current config immediately before any "
            "manual change."
        ),
    }

    if status.get("status") not in {"verified", "attention"}:
        base["blockers"].append(
            {
                "code": "status_not_eligible",
                "reason": (
                    "Operational status must be verified or attention without live drift "
                    "before a manual rollback plan can be prepared."
                ),
            }
        )
        return base

    rollback_routes = [
        item
        for item in routes
        if isinstance(item, dict) and item.get("manual_rollback_ready") is True
    ]
    if not rollback_routes:
        base["blockers"].append(
            {
                "code": "no_rollback_ready_routes",
                "reason": (
                    "No route is currently verified as ready for manual rollback."
                ),
            }
        )
        return base

    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rollback_routes:
        route = item.get("route")
        section = _ROUTE_SECTION.get(str(route))
        if section is None:
            base["blockers"].append(
                {
                    "code": "unsupported_route",
                    "category": item.get("category"),
                    "route": route,
                    "reason": "Rollback-ready route does not map to a supported Hive section.",
                }
            )
            continue
        by_section[section].append(item)

    if base["blockers"]:
        return base

    for section, items in sorted(by_section.items()):
        if len(items) > 1:
            target_keys = {
                _canonical_sha256(item.get("rollback_target"))
                for item in items
            }
            change_ids = {
                item.get("active_change_id")
                for item in items
            }
            if len(target_keys) > 1 or len(change_ids) > 1:
                base["blockers"].append(
                    {
                        "code": "ambiguous_physical_section",
                        "section": section,
                        "reason": (
                            "More than one rollback-ready route maps to the same Hive "
                            "section with different active changes or rollback targets."
                        ),
                        "claims": [
                            {
                                "category": item.get("category"),
                                "route": item.get("route"),
                                "active_change_id": item.get("active_change_id"),
                                "rollback_target": item.get("rollback_target"),
                            }
                            for item in items
                        ],
                    }
                )

    if base["blockers"]:
        return base

    merge_patch: dict[str, Any] = {}
    operations: list[dict[str, Any]] = []
    preconditions: list[dict[str, Any]] = []

    for section, items in sorted(by_section.items()):
        item = items[0]
        rollback_target = item.get("rollback_target")
        current = item.get("current_verified_config")
        actual = item.get("actual_config")

        if not isinstance(rollback_target, dict):
            base["blockers"].append(
                {
                    "code": "missing_rollback_target",
                    "section": section,
                    "reason": "Rollback-ready route is missing a rollback target.",
                }
            )
            continue
        if not isinstance(current, dict) or not isinstance(actual, dict):
            base["blockers"].append(
                {
                    "code": "missing_verified_current_state",
                    "section": section,
                    "reason": (
                        "Rollback-ready route is missing verified current/actual state."
                    ),
                }
            )
            continue

        patch = _rollback_patch(rollback_target)
        merge_patch[section] = patch
        operations.append(
            {
                "category": item.get("category"),
                "route": item.get("route"),
                "section": section,
                "active_change_id": item.get("active_change_id"),
                "from": current,
                "to": rollback_target,
                "merge_patch": patch,
            }
        )
        preconditions.append(
            {
                "section": section,
                "expected_current": actual,
                "expected_current_sha256": _canonical_sha256(actual),
                "active_change_id": item.get("active_change_id"),
            }
        )

    if base["blockers"]:
        return base

    result = {
        **base,
        "state": "ready_for_manual_rollback",
        "ready_for_manual_rollback": True,
        "merge_patch": merge_patch,
        "operations": operations,
        "preconditions": preconditions,
        "blockers": [],
    }
    result["rollback_plan_sha256"] = _canonical_sha256(result)
    return result


def render_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Hive Manual Rollback Plan",
        "",
        f"- State: **{plan['state']}**",
        (
            "- Ready for manual rollback: "
            f"**{'yes' if plan.get('ready_for_manual_rollback') else 'no'}**"
        ),
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
    ]

    if plan.get("operations"):
        lines.extend(["## Operations", ""])
        for item in plan["operations"]:
            before = item["from"]
            target = item["to"]
            lines.append(
                f"- {item['category']} / {item['route']} / {item['section']}: "
                f"{before.get('model_id')} -> {target.get('model_id')} "
                f"(change {item.get('active_change_id')})"
            )

        lines.extend(
            [
                "",
                "## Merge patch preview",
                "",
                json.dumps(plan["merge_patch"], ensure_ascii=False, indent=2),
            ]
        )

    if plan.get("blockers"):
        lines.extend(["## Blockers", ""])
        for blocker in plan["blockers"]:
            lines.append(
                f"- {blocker.get('code', 'blocked')}: "
                f"{blocker.get('reason', 'rollback plan blocked')}"
            )

    lines.extend(
        [
            "",
            "## Preconditions",
            "",
        ]
    )
    for item in plan.get("preconditions") or []:
        lines.append(
            f"- {item['section']} must still match SHA-256 "
            f"{item['expected_current_sha256']} before any manual edit."
        )

    if plan.get("rollback_plan_sha256"):
        lines.extend(
            [
                "",
                f"- Rollback plan SHA-256: {plan['rollback_plan_sha256']}",
            ]
        )

    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            plan["evidence_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionRollbackPlanError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a non-mutating manual rollback patch preview from a "
            "Hive promotion operational status bundle."
        )
    )
    parser.add_argument("--status", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 unless a conflict-free manual rollback plan is ready.",
    )
    args = parser.parse_args(argv)

    plan = build_hive_promotion_rollback_plan(
        _load_json_object(args.status, "promotion status")
    )
    markdown = render_markdown(plan)
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
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.require_ready and not plan["ready_for_manual_rollback"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
