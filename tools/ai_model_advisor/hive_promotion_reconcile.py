from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_ROUTE_SECTION = {
    "queen": "llm",
    "worker": "worker_llm",
}


class HivePromotionReconcileError(ValueError):
    """Raised when registry or Hive config reconciliation inputs are malformed."""


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


def _compare(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    differences: list[str] = []
    if actual["provider"] != expected.get("provider"):
        differences.append(
            f"provider expected {expected.get('provider')!r}, "
            f"found {actual['provider']!r}"
        )
    if actual["model"] != expected.get("model_id"):
        differences.append(
            f"model expected {expected.get('model_id')!r}, "
            f"found {actual['model']!r}"
        )

    effort = expected.get("effort")
    if effort == "default":
        if actual["reasoning_effort_key_present"]:
            differences.append(
                "reasoning_effort should be absent to use provider default"
            )
    elif actual["reasoning_effort"] != effort:
        differences.append(
            f"reasoning_effort expected {effort!r}, "
            f"found {actual['reasoning_effort']!r}"
        )

    if expected.get("execution_mode") != "single":
        differences.append(
            "registry execution_mode is not single and cannot be verified "
            "from static Hive route configuration"
        )
    return differences


def _validate_registry(registry: dict[str, Any]) -> list[dict[str, Any]]:
    if registry.get("schema_version") != 1:
        raise HivePromotionReconcileError("promotion registry schema_version must be 1")
    if registry.get("host") != "hive":
        raise HivePromotionReconcileError("promotion registry host must be hive")
    if registry.get("safe_to_auto_apply") is not False:
        raise HivePromotionReconcileError(
            "promotion registry must keep safe_to_auto_apply=false"
        )
    if registry.get("automatic_config_mutation") is not False:
        raise HivePromotionReconcileError(
            "promotion registry must keep automatic_config_mutation=false"
        )
    if registry.get("automatic_rollback") is not False:
        raise HivePromotionReconcileError(
            "promotion registry must keep automatic_rollback=false"
        )
    if registry.get("requires_human_approval") is not True:
        raise HivePromotionReconcileError(
            "promotion registry must require human approval"
        )

    routes = registry.get("routes")
    if not isinstance(routes, list):
        raise HivePromotionReconcileError(
            "promotion registry routes must be a list"
        )
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            raise HivePromotionReconcileError(
                f"promotion registry route {index} must be an object"
            )
        if route.get("route") not in _ROUTE_SECTION:
            raise HivePromotionReconcileError(
                f"promotion registry route {index} has unsupported route"
            )
        if not isinstance(route.get("category"), str) or not route["category"].strip():
            raise HivePromotionReconcileError(
                f"promotion registry route {index} must contain category"
            )
    return routes


def build_hive_promotion_reconciliation(
    registry: dict[str, Any],
    current_config: dict[str, Any],
) -> dict[str, Any]:
    """Compare checkpoint-backed registry state with current Hive route config."""
    if not isinstance(registry, dict):
        raise HivePromotionReconcileError(
            "promotion registry must be a JSON object"
        )
    if not isinstance(current_config, dict):
        raise HivePromotionReconcileError(
            "Hive configuration must be a JSON object"
        )

    routes = _validate_registry(registry)
    registry_sha = _canonical_sha256(registry)

    base: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "registry_sha256": registry_sha,
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "privacy": (
            "Reconciliation emits only provider/model/reasoning-effort route observations. "
            "Credentials, API keys, API bases, and unrelated Hive configuration are omitted."
        ),
    }

    if registry.get("status") != "ready":
        return {
            **base,
            "status": "blocked_registry",
            "verified": False,
            "reason": (
                "Promotion registry is blocked, so live configuration is not treated "
                "as reconcilable verified state."
            ),
            "routes": [],
            "drift_count": 0,
            "verified_route_count": 0,
            "unverified_route_count": 0,
            "blockers": registry.get("blockers") or [],
        }

    reports: list[dict[str, Any]] = []
    drift_count = 0
    verified_count = 0
    unverified_count = 0

    for item in routes:
        route = item["route"]
        section_name = _ROUTE_SECTION[route]
        expected = item.get("current_verified_config")

        if item.get("status") != "current_verified" or not isinstance(expected, dict):
            reports.append(
                {
                    "category": item["category"],
                    "route": route,
                    "section": section_name,
                    "state": "not_verifiable",
                    "reason": (
                        "Registry route does not provide one current_verified configuration."
                    ),
                    "expected": expected,
                    "actual": None,
                    "differences": [],
                    "active_change_id": item.get("active_change_id"),
                    "rollback_status": item.get("rollback_status"),
                    "rollback_target": item.get("rollback_target"),
                    "manual_rollback_ready": False,
                }
            )
            unverified_count += 1
            continue

        actual = _normalized_section(current_config.get(section_name))
        differences = _compare(actual, expected)
        state = "verified" if not differences else "drifted"
        if state == "verified":
            verified_count += 1
        else:
            drift_count += 1

        rollback_target = item.get("rollback_target")
        manual_rollback_ready = (
            state == "verified"
            and item.get("rollback_status") == "verified"
            and isinstance(rollback_target, dict)
        )

        reports.append(
            {
                "category": item["category"],
                "route": route,
                "section": section_name,
                "state": state,
                "reason": (
                    "Current Hive route exactly matches checkpoint-backed registry state."
                    if state == "verified"
                    else "Current Hive route differs from checkpoint-backed registry state."
                ),
                "expected": expected,
                "actual": actual,
                "differences": differences,
                "active_change_id": item.get("active_change_id"),
                "rollback_status": item.get("rollback_status"),
                "rollback_target": rollback_target,
                "manual_rollback_ready": manual_rollback_ready,
            }
        )

    if drift_count:
        status = "drifted"
        verified = False
        reason = (
            "At least one current Hive route differs from checkpoint-backed registry state."
        )
    elif unverified_count:
        status = "partial"
        verified = False
        reason = (
            "No drift was found among verifiable routes, but at least one registry route "
            "does not establish a single current configuration."
        )
    else:
        status = "verified"
        verified = True
        reason = (
            "Every registry route with current state exactly matches current Hive configuration."
        )

    observations = {
        report["section"]: report["actual"]
        for report in reports
        if isinstance(report.get("actual"), dict)
    }

    return {
        **base,
        "status": status,
        "verified": verified,
        "reason": reason,
        "routes": reports,
        "drift_count": drift_count,
        "verified_route_count": verified_count,
        "unverified_route_count": unverified_count,
        "route_observations_sha256": _canonical_sha256(observations),
        "blockers": [],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Registry Reconciliation",
        "",
        f"- Status: **{report['status']}**",
        f"- Verified: **{'yes' if report.get('verified') else 'no'}**",
        f"- Verified routes: **{report.get('verified_route_count', 0)}**",
        f"- Drifted routes: **{report.get('drift_count', 0)}**",
        f"- Unverified routes: **{report.get('unverified_route_count', 0)}**",
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        report["reason"],
    ]

    routes = report.get("routes") or []
    if routes:
        lines.extend(
            [
                "",
                "## Routes",
                "",
                "| Category | Route | State | Expected | Actual | Manual rollback |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in routes:
            expected = item.get("expected")
            actual = item.get("actual")
            expected_text = (
                "-"
                if not isinstance(expected, dict)
                else (
                    f"{expected.get('provider')} / {expected.get('model_id')} / "
                    f"{expected.get('effort')} / {expected.get('execution_mode')}"
                )
            )
            actual_text = (
                "-"
                if not isinstance(actual, dict)
                else (
                    f"{actual.get('provider')} / {actual.get('model')} / "
                    f"{actual.get('reasoning_effort')}"
                )
            )
            lines.append(
                f"| {item['category']} | {item['route']} | "
                f"**{item['state']}** | {expected_text} | {actual_text} | "
                f"{'ready' if item.get('manual_rollback_ready') else 'not ready'} |"
            )

            if item.get("differences"):
                for difference in item["differences"]:
                    lines.append(f"- {item['category']} / {item['route']}: {difference}")

    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(["", "## Registry blockers", ""])
        for blocker in blockers:
            lines.append(
                f"- {blocker.get('code', 'blocked')}: "
                f"{blocker.get('reason', 'registry blocked')}"
            )

    lines.extend(
        [
            "",
            f"- Registry SHA-256: {report['registry_sha256']}",
            "",
            report["privacy"],
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionReconcileError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile checkpoint-backed Hive promotion registry state with "
            "the current Hive configuration."
        )
    )
    parser.add_argument("--registry", required=True)
    parser.add_argument("--hive-config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit 2 unless every registry route is exactly verified.",
    )
    args = parser.parse_args(argv)

    report = build_hive_promotion_reconciliation(
        _load_json_object(args.registry, "promotion registry"),
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

    if args.require_verified and not report["verified"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
