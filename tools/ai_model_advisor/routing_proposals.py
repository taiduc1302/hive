from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore


def _config_key(config: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not config:
        return None
    return (
        str(config.get("model_id", "")),
        str(config.get("effort", "")),
        str(config.get("execution_mode", "")),
    )


def _current_config(row: dict[str, Any]) -> dict[str, Any]:
    primary = row["primary"]
    return {
        "provider": primary.get("provider"),
        "model_id": primary["model_id"],
        "label": primary.get("label", primary["model_id"]),
        "effort": primary["effort"],
        "execution_mode": primary["execution_mode"],
        "router_score": primary.get("score"),
        "router_confidence": primary.get("confidence"),
    }


def _proposal_confidence(decision: dict[str, Any]) -> dict[str, Any]:
    status = decision["status"]
    winner = decision.get("winner") or {}
    runner = decision.get("runner_up") or {}
    score_margin = decision.get("score_margin")
    quality_margin = decision.get("quality_margin")

    if status != "promote":
        return {"level": "low", "score": 0.25}

    evidence_floor = min(
        int(winner.get("observations", 0)),
        int(runner.get("observations", 0)),
    )
    balanced_efficiency = bool(
        winner.get("efficiency_ready") and runner.get("efficiency_ready")
    )
    strong_margin = bool(
        (quality_margin is not None and quality_margin >= 2.0)
        or (score_margin is not None and score_margin >= 2.0 and balanced_efficiency)
    )
    if strong_margin and evidence_floor >= 6:
        return {"level": "high", "score": 0.9}
    return {"level": "medium", "score": 0.7}


def build_routing_proposals(
    routing_matrix: list[dict[str, Any]],
    leaderboard: dict[str, Any],
) -> dict[str, Any]:
    """Translate empirical leaderboard decisions into reviewable router proposals.

    This function never mutates router policy. Even a `propose_change` result
    requires an explicit human review and a separate policy edit.
    """
    empirical_by_category = {
        item["category"]: item for item in leaderboard.get("categories", [])
    }
    proposals: list[dict[str, Any]] = []

    for row in routing_matrix:
        category = row["category"]
        current = _current_config(row)
        empirical = empirical_by_category.get(category)
        if empirical is None:
            proposals.append(
                {
                    "category": category,
                    "action": "insufficient_evidence",
                    "current": current,
                    "candidate": None,
                    "confidence": {"level": "low", "score": 0.0},
                    "reason": "No controlled empirical leaderboard evidence exists for this routing category.",
                    "evidence": None,
                    "safe_to_apply": False,
                    "requires_human_review": False,
                }
            )
            continue

        decision = empirical["decision"]
        winner = decision.get("winner")
        status = decision["status"]
        current_key = _config_key(current)
        winner_key = _config_key(winner)

        if status == "promote" and winner is not None:
            if current_key == winner_key:
                action = "keep"
                reason = "The current router primary already matches the empirically promoted configuration."
                requires_review = False
            else:
                action = "propose_change"
                reason = (
                    "The empirical leaderboard promotes a different controlled configuration. "
                    "Review the evidence before editing router policy."
                )
                requires_review = True
            confidence = _proposal_confidence(decision)
        elif status == "hold":
            action = "keep"
            reason = "The leading controlled configurations are too close to justify a routing change."
            confidence = {"level": "medium", "score": 0.6}
            requires_review = False
        elif status == "collect_more":
            action = "collect_more"
            reason = (
                "A meaningful empirical gap exists, but balanced evidence is still insufficient for a safe promotion."
            )
            confidence = {"level": "low", "score": 0.4}
            requires_review = False
        else:
            action = "insufficient_evidence"
            reason = (
                "At least two exact controlled configurations are required before proposing a routing change."
            )
            confidence = {"level": "low", "score": 0.25}
            requires_review = False

        proposals.append(
            {
                "category": category,
                "action": action,
                "current": current,
                "candidate": winner,
                "confidence": confidence,
                "reason": reason,
                "evidence": {
                    "leaderboard_status": status,
                    "leaderboard_reason": decision.get("reason"),
                    "score_margin": decision.get("score_margin"),
                    "quality_margin": decision.get("quality_margin"),
                    "eligible_configurations": empirical.get("eligible_configurations", 0),
                },
                "safe_to_apply": False,
                "requires_human_review": requires_review,
            }
        )

    counts = {
        action: sum(1 for item in proposals if item["action"] == action)
        for action in ("propose_change", "keep", "collect_more", "insufficient_evidence")
    }
    return {
        "records": leaderboard.get("records", 0),
        "proposals": proposals,
        "action_counts": counts,
        "automatic_policy_mutation": False,
    }


def routing_proposals_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Routing Proposals",
        "",
        "These proposals are review-only. This command never edits router policy automatically.",
        "",
        "| Category | Action | Current | Candidate | Confidence | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for proposal in report["proposals"]:
        current = proposal["current"]
        candidate = proposal["candidate"]
        current_label = f"`{current['model_id']} / {current['effort']} / {current['execution_mode']}`"
        candidate_label = "—"
        if candidate:
            candidate_label = (
                f"`{candidate['model_id']} / {candidate['effort']} / {candidate['execution_mode']}`"
            )
        evidence = proposal["evidence"]
        if evidence:
            detail = f"{evidence['leaderboard_status']}"
            if evidence["score_margin"] is not None:
                detail += f"; score Δ {evidence['score_margin']:+.3f}"
            if evidence["quality_margin"] is not None:
                detail += f"; quality Δ {evidence['quality_margin']:+.3f}"
        else:
            detail = "none"
        lines.append(
            f"| {proposal['category']} | **{proposal['action']}** | {current_label} | "
            f"{candidate_label} | {proposal['confidence']['level']} | {detail} |"
        )

    lines.extend(["", "## Review notes", ""])
    for proposal in report["proposals"]:
        lines.append(f"- **{proposal['category']}**: {proposal['reason']}")
    lines.extend(
        [
            "",
            (
                "`safe_to_apply` is always `false` in this report. A proposed change "
                "requires a separate, explicit router-policy edit and validation run."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build review-only routing proposals from router output and empirical feedback."
    )
    parser.add_argument("--routing-matrix", required=True, help="JSON output from Advisor matrix command")
    parser.add_argument("--feedback", required=True, help="Feedback JSONL path")
    parser.add_argument("--output", help="Markdown output path")
    parser.add_argument("--json-output", help="JSON output path")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.routing_matrix).read_text(encoding="utf-8"))
    rows = payload.get("routing_matrix", payload)
    if not isinstance(rows, list):
        raise ValueError("routing matrix JSON must be a list or contain a routing_matrix list")

    leaderboard = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    report = build_routing_proposals(rows, leaderboard)
    markdown = routing_proposals_markdown(report)
    _write(args.output, markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not args.output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
