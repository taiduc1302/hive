from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_REVIEW_STATES = (
    "ready_for_manual_edit",
    "blocked_route_drift",
    "blocked_missing_route",
    "blocked_inconsistent_canary",
    "blocked_no_change",
    "not_ready",
    "do_not_promote",
)


def _config_key(config: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not config:
        return None
    model_id = str(config.get("model_id", "")).strip()
    effort = str(config.get("effort", "")).strip()
    execution_mode = str(config.get("execution_mode", "")).strip()
    if not model_id or not effort or not execution_mode:
        return None
    return model_id, effort, execution_mode


def _compact_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not config:
        return None
    payload = {
        "provider": config.get("provider"),
        "model_id": config.get("model_id"),
        "label": config.get("label"),
        "effort": config.get("effort"),
        "execution_mode": config.get("execution_mode"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _route_index(routing_matrix: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    routes: dict[str, dict[str, Any]] = {}
    for row in routing_matrix:
        category = str(row.get("category", "")).strip()
        if not category:
            raise ValueError("routing matrix row is missing category")
        if category in routes:
            raise ValueError(f"routing matrix contains duplicate category: {category}")
        routes[category] = row
    return routes


def _change_id(
    category: str,
    current: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "category": category,
            "current": _config_key(current),
            "candidate": _config_key(candidate),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{category}-{digest}"


def _canary_is_consistent(item: dict[str, Any]) -> bool:
    required_pairs = int(item.get("required_pairs", 0))
    matched_pairs = int(item.get("matched_pairs", 0))
    return bool(
        item.get("state") == "eligible_for_manual_promotion"
        and item.get("safe_to_apply") is False
        and item.get("requires_human_approval") is True
        and required_pairs >= 3
        and matched_pairs >= required_pairs
        and item.get("leaderboard_status") == "promote"
        and item.get("winner_matches_candidate") is True
    )


def _review_item(
    evaluation: dict[str, Any],
    route: dict[str, Any] | None,
) -> dict[str, Any]:
    category = str(evaluation.get("category", "")).strip()
    current = evaluation.get("current") or {}
    candidate = evaluation.get("candidate") or {}
    current_key = _config_key(current)
    candidate_key = _config_key(candidate)
    live_primary = (route or {}).get("primary") or {}
    live_key = _config_key(live_primary)
    source_state = str(evaluation.get("state", ""))

    base: dict[str, Any] = {
        "category": category,
        "source_state": source_state,
        "current": _compact_config(current),
        "candidate": _compact_config(candidate),
        "live_primary": _compact_config(live_primary),
        "required_pairs": int(evaluation.get("required_pairs", 0)),
        "matched_pairs": int(evaluation.get("matched_pairs", 0)),
        "safe_to_apply": False,
        "automatic_policy_mutation": False,
        "requires_human_approval": False,
        "manual_change": None,
    }

    if source_state == "rollback_candidate":
        return {
            **base,
            "state": "do_not_promote",
            "reason": "Fresh canary evidence says to roll back or stop the candidate.",
        }
    if source_state != "eligible_for_manual_promotion":
        return {
            **base,
            "state": "not_ready",
            "reason": "The canary has not made this candidate eligible for manual promotion.",
        }
    if not _canary_is_consistent(evaluation):
        return {
            **base,
            "state": "blocked_inconsistent_canary",
            "reason": (
                "The canary payload is internally inconsistent with an eligible "
                "manual-promotion decision."
            ),
        }
    if current_key is None or candidate_key is None:
        return {
            **base,
            "state": "blocked_inconsistent_canary",
            "reason": "Current or candidate configuration is incomplete.",
        }
    if route is None or live_key is None:
        return {
            **base,
            "state": "blocked_missing_route",
            "reason": "No current routing-matrix primary exists for this category.",
        }
    if live_key != current_key:
        return {
            **base,
            "state": "blocked_route_drift",
            "reason": (
                "The live routing-matrix primary changed after the canary plan was created. "
                "Re-plan and re-validate before any manual edit."
            ),
        }
    if candidate_key == current_key:
        return {
            **base,
            "state": "blocked_no_change",
            "reason": (
                "Candidate and current route are identical, so there is no promotion "
                "change to review."
            ),
        }

    before = _compact_config(live_primary) or {}
    after = _compact_config(candidate) or {}
    rollback_to = _compact_config(current) or {}
    manual_change = {
        "change_id": _change_id(category, current, candidate),
        "category": category,
        "before": before,
        "after": after,
        "rollback_to": rollback_to,
        "review_checklist": [
            "Confirm the canary evaluation came from fresh matched task IDs.",
            "Confirm the current routing primary still exactly matches the before configuration.",
            "Confirm the candidate model and execution controls are available on the intended runtime target.",
            "Apply the routing edit manually in the operator-owned routing layer.",
            "Re-run Advisor and repository validation after the edit.",
            "Keep the before configuration as the explicit rollback target.",
        ],
    }
    return {
        **base,
        "state": "ready_for_manual_edit",
        "reason": (
            "Fresh canary evidence is eligible and the live routing primary still "
            "matches the exact pre-canary route."
        ),
        "requires_human_approval": True,
        "manual_change": manual_change,
    }


def build_promotion_review(
    canary_report: dict[str, Any],
    routing_matrix: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a fail-closed manual promotion package without mutating routing policy."""
    if canary_report.get("automatic_policy_mutation") is True:
        raise ValueError("canary report must not enable automatic policy mutation")
    if canary_report.get("automatic_rollback") is True:
        raise ValueError("canary report must not enable automatic rollback")

    routes = _route_index(routing_matrix)
    reviews = [
        _review_item(item, routes.get(str(item.get("category", "")).strip()))
        for item in canary_report.get("evaluations", [])
    ]
    return {
        "reviews": reviews,
        "state_counts": {
            state: sum(item["state"] == state for item in reviews)
            for state in _REVIEW_STATES
        },
        "ready_changes": sum(
            item["state"] == "ready_for_manual_edit" for item in reviews
        ),
        "safe_to_apply": False,
        "automatic_policy_mutation": False,
        "automatic_rollback": False,
    }


def _config_label(config: dict[str, Any] | None) -> str:
    if not config:
        return "—"
    return (
        f"{config.get('model_id')} / {config.get('effort')} / "
        f"{config.get('execution_mode')}"
    )


def promotion_review_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Promotion Review",
        "",
        "This is a review package only. It never edits routing policy automatically.",
        "",
        "| Category | Review state | Live current | Candidate | Matched / Required |",
        "|---|---|---|---|---:|",
    ]
    for item in report["reviews"]:
        lines.append(
            f"| {item['category']} | **{item['state']}** | "
            f"{_config_label(item.get('live_primary'))} | "
            f"{_config_label(item.get('candidate'))} | "
            f"{item.get('matched_pairs', 0)} / {item.get('required_pairs', 0)} |"
        )

    for item in report["reviews"]:
        lines.extend(["", f"## {item['category']}", "", item["reason"], ""])
        change = item.get("manual_change")
        if not change:
            continue
        lines.extend(
            [
                f"- Change ID: {change['change_id']}",
                f"- Before: {_config_label(change['before'])}",
                f"- After: {_config_label(change['after'])}",
                f"- Rollback target: {_config_label(change['rollback_to'])}",
                "",
                "### Human review checklist",
                "",
            ]
        )
        lines.extend(f"- {entry}" for entry in change["review_checklist"])

    lines.extend(
        [
            "",
            "safe_to_apply remains false. A ready package still requires an explicit "
            "human edit and validation.",
            "",
        ]
    )
    return "\n".join(lines)


def _routing_rows(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("routing_matrix", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(
            "routing matrix JSON must be a list or contain a routing_matrix list"
        )
    return rows


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed manual routing-promotion review package."
    )
    parser.add_argument("--canary-evaluation", required=True)
    parser.add_argument("--routing-matrix", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    canary_report = json.loads(
        Path(args.canary_evaluation).read_text(encoding="utf-8")
    )
    if not isinstance(canary_report, dict):
        raise ValueError("canary evaluation root must be a JSON object")
    report = build_promotion_review(
        canary_report,
        _routing_rows(args.routing_matrix),
    )
    markdown = promotion_review_markdown(report)
    _write(args.output, markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    if not args.output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
