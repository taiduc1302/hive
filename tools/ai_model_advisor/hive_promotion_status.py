from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .hive_promotion_reconcile import (
    build_hive_promotion_reconciliation,
)
from .hive_promotion_registry import build_hive_promotion_registry


class HivePromotionStatusError(ValueError):
    """Raised when Hive promotion operational status inputs are malformed."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _route_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("category", "")), str(item.get("route", ""))


def _status_and_action(
    registry: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    pending_count: int,
) -> tuple[str, str]:
    if registry.get("status") != "ready":
        return (
            "blocked",
            "resolve_evidence_blockers",
        )
    reconcile_status = reconciliation.get("status")
    if reconcile_status == "drifted":
        return (
            "drifted",
            "investigate_live_config_drift",
        )
    if reconcile_status in {"partial", "blocked_registry"}:
        return (
            "attention",
            "resolve_unverified_routes",
        )
    if pending_count:
        return (
            "attention",
            "review_pending_promotions",
        )
    if reconciliation.get("verified") is True:
        return (
            "verified",
            "none",
        )
    return (
        "attention",
        "review_status_evidence",
    )


def build_hive_promotion_status(
    snapshots: list[tuple[dict[str, Any], dict[str, Any]]],
    current_config: dict[str, Any],
) -> dict[str, Any]:
    """Build one read-only operator view across registry and live reconciliation."""
    if not isinstance(snapshots, list) or not snapshots:
        raise HivePromotionStatusError(
            "promotion status requires at least one journal/checkpoint snapshot"
        )
    if not isinstance(current_config, dict):
        raise HivePromotionStatusError(
            "Hive configuration must be a JSON object"
        )

    registry = build_hive_promotion_registry(snapshots)
    reconciliation = build_hive_promotion_reconciliation(
        registry,
        current_config,
    )

    registry_routes = {
        _route_key(item): item
        for item in registry.get("routes", [])
        if isinstance(item, dict)
    }
    reconciliation_routes = {
        _route_key(item): item
        for item in reconciliation.get("routes", [])
        if isinstance(item, dict)
    }

    route_keys = sorted(set(registry_routes) | set(reconciliation_routes))
    routes: list[dict[str, Any]] = []
    pending_count = 0
    rollback_ready_count = 0
    drift_count = 0

    for key in route_keys:
        registry_route = registry_routes.get(key, {})
        live_route = reconciliation_routes.get(key, {})
        pending = registry_route.get("pending_previews") or []
        if not isinstance(pending, list):
            pending = []
        pending_count += len(pending)

        manual_rollback_ready = bool(
            live_route.get("manual_rollback_ready")
        )
        if manual_rollback_ready:
            rollback_ready_count += 1

        live_state = live_route.get("state", "not_reconciled")
        if live_state == "drifted":
            drift_count += 1

        routes.append(
            {
                "category": key[0],
                "route": key[1],
                "registry_status": registry_route.get("status"),
                "live_state": live_state,
                "current_verified_config": registry_route.get(
                    "current_verified_config"
                ),
                "actual_config": live_route.get("actual"),
                "active_change_id": registry_route.get("active_change_id"),
                "rollback_status": registry_route.get("rollback_status"),
                "rollback_target": registry_route.get("rollback_target"),
                "manual_rollback_ready": manual_rollback_ready,
                "pending_promotions": pending,
                "differences": live_route.get("differences") or [],
            }
        )

    status, operator_action = _status_and_action(
        registry,
        reconciliation,
        pending_count=pending_count,
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "status": status,
        "operator_action": operator_action,
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "snapshot_count": registry.get("snapshot_count", len(snapshots)),
        "checkpoint_verified_snapshot_count": registry.get(
            "checkpoint_verified_snapshot_count",
            0,
        ),
        "selected_change_count": registry.get("selected_change_count", 0),
        "route_count": len(routes),
        "verified_route_count": reconciliation.get(
            "verified_route_count",
            0,
        ),
        "drift_count": drift_count,
        "unverified_route_count": reconciliation.get(
            "unverified_route_count",
            0,
        ),
        "pending_promotion_count": pending_count,
        "manual_rollback_ready_count": rollback_ready_count,
        "routes": routes,
        "blockers": registry.get("blockers") or [],
        "evidence": {
            "registry_sha256": registry.get("registry_sha256"),
            "reconciliation_sha256": _canonical_sha256(reconciliation),
            "route_observations_sha256": reconciliation.get(
                "route_observations_sha256"
            ),
        },
        "evidence_boundary": (
            "This bundle summarizes checkpoint-backed promotion evidence and one "
            "specific supplied Hive configuration. It does not establish trusted "
            "time ordering beyond the underlying evidence, and it performs no "
            "configuration mutation or rollback."
        ),
    }
    payload["status_bundle_sha256"] = _canonical_sha256(payload)
    return payload


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Operational Status",
        "",
        f"- Status: **{report['status']}**",
        f"- Operator action: **{report['operator_action']}**",
        f"- Verified routes: **{report['verified_route_count']}**",
        f"- Drifted routes: **{report['drift_count']}**",
        f"- Unverified routes: **{report['unverified_route_count']}**",
        f"- Pending promotions: **{report['pending_promotion_count']}**",
        (
            "- Manual rollback ready routes: "
            f"**{report['manual_rollback_ready_count']}**"
        ),
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        "## Routes",
        "",
        (
            "| Category | Route | Registry | Live | Current verified | "
            "Active change | Rollback | Pending |"
        ),
        "|---|---|---|---|---|---|---|---:|",
    ]

    for item in report["routes"]:
        current = item.get("current_verified_config")
        current_text = (
            "-"
            if not isinstance(current, dict)
            else (
                f"{current.get('provider')} / {current.get('model_id')} / "
                f"{current.get('effort')}"
            )
        )
        rollback_text = (
            "ready"
            if item.get("manual_rollback_ready")
            else item.get("rollback_status") or "not_available"
        )
        lines.append(
            f"| {item['category']} | {item['route']} | "
            f"{item.get('registry_status') or '-'} | "
            f"{item.get('live_state') or '-'} | {current_text} | "
            f"{item.get('active_change_id') or '-'} | {rollback_text} | "
            f"{len(item.get('pending_promotions') or [])} |"
        )
        for difference in item.get("differences") or []:
            lines.append(
                f"- {item['category']} / {item['route']} drift: {difference}"
            )

    if report["blockers"]:
        lines.extend(["", "## Evidence blockers", ""])
        for blocker in report["blockers"]:
            lines.append(
                f"- {blocker.get('code', 'blocked')}: "
                f"{blocker.get('reason', 'registry evidence is blocked')}"
            )

    pending_routes = [
        item
        for item in report["routes"]
        if item.get("pending_promotions")
    ]
    if pending_routes:
        lines.extend(["", "## Pending promotions", ""])
        for item in pending_routes:
            for pending in item["pending_promotions"]:
                after = pending.get("after") or {}
                lines.append(
                    f"- {item['category']} / {item['route']}: "
                    f"{pending.get('change_id')} -> "
                    f"{after.get('model_id')} / {after.get('effort')}"
                )

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Registry SHA-256: {report['evidence']['registry_sha256']}",
            (
                "- Reconciliation SHA-256: "
                f"{report['evidence']['reconciliation_sha256']}"
            ),
            f"- Status bundle SHA-256: {report['status_bundle_sha256']}",
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
        raise HivePromotionStatusError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one read-only operator status bundle from checkpoint-backed "
            "promotion journals and current Hive configuration."
        )
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("JOURNAL", "CHECKPOINT"),
        required=True,
        help="Journal/checkpoint pair. Repeat for additional snapshots.",
    )
    parser.add_argument("--hive-config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit 2 unless the final operational status is verified.",
    )
    args = parser.parse_args(argv)

    snapshots = [
        (
            _load_json_object(journal_path, "promotion journal"),
            _load_json_object(checkpoint_path, "promotion checkpoint"),
        )
        for journal_path, checkpoint_path in args.pair
    ]
    report = build_hive_promotion_status(
        snapshots,
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

    if args.require_verified and report["status"] != "verified":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
