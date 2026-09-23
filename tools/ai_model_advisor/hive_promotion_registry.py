from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .hive_promotion_checkpoint import (
    HivePromotionCheckpointError,
    verify_hive_promotion_checkpoint,
)
from .hive_promotion_journal import (
    HivePromotionJournalError,
    validate_hive_promotion_journal,
)

_SCOPE_ROUTES = {
    "queen": ("queen",),
    "worker": ("worker",),
    "both": ("queen", "worker"),
}
_STATE_RANK = {
    "previewed": 0,
    "applied_verified": 1,
    "rolled_back_verified": 2,
}


class HivePromotionRegistryError(ValueError):
    """Raised when Hive promotion registry inputs are malformed."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _journal_summary(
    journal: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    return {
        "category": journal["category"],
        "change_id": journal["change_id"],
        "scope": journal["scope"],
        "state": journal["state"],
        "entry_count": len(journal["entries"]),
        "head_entry_sha256": journal["head_entry_sha256"],
        "journal_sha256": checkpoint["journal_sha256"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "transition": journal["transition"],
        "routes": list(_SCOPE_ROUTES[journal["scope"]]),
    }


def _snapshot_chain_is_prefix(
    older: dict[str, Any],
    newer: dict[str, Any],
) -> bool:
    if older["category"] != newer["category"]:
        return False
    if older["change_id"] != newer["change_id"]:
        return False
    if older["scope"] != newer["scope"]:
        return False
    if older["transition"] != newer["transition"]:
        return False

    older_entries = older["entries"]
    newer_entries = newer["entries"]
    if len(older_entries) > len(newer_entries):
        return False
    return older_entries == newer_entries[: len(older_entries)]


def _select_latest_snapshots(
    verified: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in verified:
        grouped[item["journal"]["change_id"]].append(item)

    selected: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    for change_id, items in grouped.items():
        metadata = {
            (
                item["journal"]["category"],
                item["journal"]["scope"],
                _canonical_key(item["journal"]["transition"]),
            )
            for item in items
        }
        if len(metadata) != 1:
            blockers.append(
                {
                    "code": "conflicting_change_identity",
                    "change_id": change_id,
                    "reason": (
                        "Multiple checkpoint-verified snapshots reuse the same change_id "
                        "with different category, scope, or transition metadata."
                    ),
                }
            )
            continue

        by_count: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_count[len(item["journal"]["entries"])].append(item)

        conflicting_equal_depth = False
        for count, same_depth in by_count.items():
            hashes = {
                item["checkpoint"]["journal_sha256"]
                for item in same_depth
            }
            if len(hashes) > 1:
                blockers.append(
                    {
                        "code": "divergent_same_depth_snapshot",
                        "change_id": change_id,
                        "entry_count": count,
                        "reason": (
                            "Multiple checkpoint-verified journals for the same change "
                            "have equal entry counts but different content."
                        ),
                    }
                )
                conflicting_equal_depth = True
        if conflicting_equal_depth:
            continue

        ordered = sorted(
            items,
            key=lambda item: (
                len(item["journal"]["entries"]),
                _STATE_RANK.get(item["journal"]["state"], -1),
            ),
        )
        latest = ordered[-1]
        chain_ok = all(
            _snapshot_chain_is_prefix(item["journal"], latest["journal"])
            for item in ordered[:-1]
        )
        if not chain_ok:
            blockers.append(
                {
                    "code": "divergent_snapshot_history",
                    "change_id": change_id,
                    "reason": (
                        "An older checkpoint-verified snapshot is not an exact prefix "
                        "of the most advanced journal for this change."
                    ),
                }
            )
            continue
        selected.append(latest)

    selected.sort(
        key=lambda item: (
            item["journal"]["category"],
            item["journal"]["scope"],
            item["journal"]["change_id"],
        )
    )
    return selected, blockers


def _route_report(
    category: str,
    route: str,
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    summaries = [
        _journal_summary(item["journal"], item["checkpoint"])
        for item in items
    ]

    current_claims: list[dict[str, Any]] = []
    active_claims: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    for item in items:
        journal = item["journal"]
        state = journal["state"]
        transition = journal["transition"]
        if state == "previewed":
            pending.append(
                {
                    "change_id": journal["change_id"],
                    "checkpoint_sha256": item["checkpoint"]["checkpoint_sha256"],
                    "before": transition["before"],
                    "after": transition["after"],
                }
            )
        elif state == "applied_verified":
            current_claims.append(
                {
                    "change_id": journal["change_id"],
                    "config": transition["after"],
                    "state": state,
                    "checkpoint_sha256": item["checkpoint"]["checkpoint_sha256"],
                }
            )
            active_claims.append(
                {
                    "change_id": journal["change_id"],
                    "current": transition["after"],
                    "rollback_to": transition["rollback_to"],
                    "checkpoint_sha256": item["checkpoint"]["checkpoint_sha256"],
                }
            )
        elif state == "rolled_back_verified":
            current_claims.append(
                {
                    "change_id": journal["change_id"],
                    "config": transition["rollback_to"],
                    "state": state,
                    "checkpoint_sha256": item["checkpoint"]["checkpoint_sha256"],
                }
            )

    unique_current = {
        _canonical_key(claim["config"]): claim["config"]
        for claim in current_claims
    }
    current_verified_config: dict[str, str] | None = None
    route_status = "pending_only" if pending else "no_evidence"

    if len(unique_current) == 1:
        current_verified_config = next(iter(unique_current.values()))
        route_status = "current_verified"
    elif len(unique_current) > 1:
        route_status = "blocked_conflicting_current_state"
        blockers.append(
            {
                "code": "conflicting_current_state",
                "category": category,
                "route": route,
                "reason": (
                    "Checkpoint-verified journals claim different current configurations "
                    "for the same category and Hive route."
                ),
                "claims": current_claims,
            }
        )

    matching_active = []
    if current_verified_config is not None:
        matching_active = [
            claim
            for claim in active_claims
            if claim["current"] == current_verified_config
        ]

    active_change_id: str | None = None
    rollback_target: dict[str, str] | None = None
    rollback_status = "not_available"

    if len(matching_active) == 1:
        active_change_id = matching_active[0]["change_id"]
        rollback_target = matching_active[0]["rollback_to"]
        rollback_status = "verified"
    elif len(matching_active) > 1:
        rollback_status = "blocked_ambiguous"
        blockers.append(
            {
                "code": "ambiguous_active_promotion",
                "category": category,
                "route": route,
                "reason": (
                    "More than one applied_verified promotion claims the same current "
                    "configuration, so the active change and rollback target are ambiguous."
                ),
                "claims": matching_active,
            }
        )
        if route_status == "current_verified":
            route_status = "blocked_ambiguous_active_promotion"

    return (
        {
            "category": category,
            "route": route,
            "status": route_status,
            "current_verified_config": current_verified_config,
            "active_change_id": active_change_id,
            "rollback_status": rollback_status,
            "rollback_target": rollback_target,
            "pending_previews": pending,
            "evidence": summaries,
        },
        blockers,
    )


def build_hive_promotion_registry(
    snapshots: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Aggregate checkpoint-verified promotion journals without guessing chronology."""
    if not isinstance(snapshots, list) or not snapshots:
        raise HivePromotionRegistryError(
            "promotion registry requires at least one journal/checkpoint snapshot"
        )

    verified: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    for index, pair in enumerate(snapshots):
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise HivePromotionRegistryError(
                f"snapshot {index} must be a (journal, checkpoint) tuple"
            )
        journal, checkpoint = pair
        if not isinstance(journal, dict) or not isinstance(checkpoint, dict):
            raise HivePromotionRegistryError(
                f"snapshot {index} journal and checkpoint must be JSON objects"
            )
        try:
            validate_hive_promotion_journal(journal)
            verification = verify_hive_promotion_checkpoint(journal, checkpoint)
        except (HivePromotionJournalError, HivePromotionCheckpointError) as exc:
            raise HivePromotionRegistryError(
                f"snapshot {index} is malformed: {exc}"
            ) from exc

        if not verification["verified"]:
            blockers.append(
                {
                    "code": "checkpoint_mismatch",
                    "snapshot_index": index,
                    "category": journal.get("category"),
                    "change_id": journal.get("change_id"),
                    "scope": journal.get("scope"),
                    "reason": (
                        "Journal does not exactly match its supplied independent checkpoint."
                    ),
                    "mismatches": verification["mismatches"],
                }
            )
            continue
        verified.append(
            {
                "journal": journal,
                "checkpoint": checkpoint,
                "verification": verification,
            }
        )

    selected, snapshot_blockers = _select_latest_snapshots(verified)
    blockers.extend(snapshot_blockers)

    by_route: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        journal = item["journal"]
        for route in _SCOPE_ROUTES[journal["scope"]]:
            by_route[(journal["category"], route)].append(item)

    routes: list[dict[str, Any]] = []
    for (category, route), items in sorted(by_route.items()):
        report, route_blockers = _route_report(category, route, items)
        routes.append(report)
        blockers.extend(route_blockers)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "status": "ready" if not blockers else "blocked",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "snapshot_count": len(snapshots),
        "checkpoint_verified_snapshot_count": len(verified),
        "selected_change_count": len(selected),
        "routes": routes,
        "blockers": blockers,
        "evidence_boundary": (
            "The registry only treats exact checkpoint-matched journal snapshots as "
            "verified evidence. It does not infer ordering between independent changes. "
            "Conflicting current-state or rollback claims are blocked rather than ranked."
        ),
    }
    payload["registry_sha256"] = _canonical_sha256(payload)
    return payload


def render_markdown(registry: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Registry",
        "",
        f"- Status: **{registry['status']}**",
        f"- Supplied snapshots: **{registry['snapshot_count']}**",
        (
            "- Checkpoint-verified snapshots: "
            f"**{registry['checkpoint_verified_snapshot_count']}**"
        ),
        f"- Selected changes: **{registry['selected_change_count']}**",
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        "## Routes",
        "",
        "| Category | Route | Status | Current verified | Active change | Rollback |",
        "|---|---|---|---|---|---|",
    ]

    for route in registry["routes"]:
        current = route["current_verified_config"]
        current_text = (
            "-"
            if current is None
            else (
                f"{current['provider']} / {current['model_id']} / "
                f"{current['effort']} / {current['execution_mode']}"
            )
        )
        rollback = route["rollback_target"]
        rollback_text = (
            route["rollback_status"]
            if rollback is None
            else (
                f"{rollback['provider']} / {rollback['model_id']} / "
                f"{rollback['effort']} / {rollback['execution_mode']}"
            )
        )
        lines.append(
            f"| {route['category']} | {route['route']} | "
            f"**{route['status']}** | {current_text} | "
            f"{route['active_change_id'] or '-'} | {rollback_text} |"
        )

        if route["pending_previews"]:
            lines.append("")
            lines.append(
                f"Pending previews for **{route['category']} / {route['route']}**:"
            )
            for pending in route["pending_previews"]:
                lines.append(
                    f"- {pending['change_id']} -> "
                    f"{pending['after']['model_id']} / {pending['after']['effort']}"
                )

    if registry["blockers"]:
        lines.extend(["", "## Blockers", ""])
        for blocker in registry["blockers"]:
            context = " / ".join(
                str(blocker[key])
                for key in ("category", "route", "change_id")
                if blocker.get(key)
            )
            prefix = f" ({context})" if context else ""
            lines.append(
                f"- {blocker['code']}{prefix}: {blocker['reason']}"
            )

    lines.extend(
        [
            "",
            f"- Registry SHA-256: {registry['registry_sha256']}",
            "",
            "## Evidence boundary",
            "",
            registry["evidence_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionRegistryError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a fail-closed operational registry from independently "
            "checkpointed Hive promotion journals."
        )
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("JOURNAL", "CHECKPOINT"),
        required=True,
        help="Journal/checkpoint pair. Repeat for additional promotion snapshots.",
    )
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 when any registry blocker is present.",
    )
    args = parser.parse_args(argv)

    snapshots = [
        (
            _load_json_object(journal_path, "promotion journal"),
            _load_json_object(checkpoint_path, "promotion checkpoint"),
        )
        for journal_path, checkpoint_path in args.pair
    ]
    registry = build_hive_promotion_registry(snapshots)
    markdown = render_markdown(registry)

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
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.require_ready and registry["status"] != "ready":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
