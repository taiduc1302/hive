from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .empirical_leaderboard import (
    MAX_QUALITY_REGRESSION_FOR_EFFICIENCY_PROMOTION,
    PROMOTION_SCORE_MARGIN,
    QUALITY_PROMOTION_MARGIN,
)
from .feedback import EXACT_FEEDBACK_MIN, PAIRED_EFFICIENCY_MIN

MIN_FRESH_CANARY_PAIRS = 3
MEDIUM_CONFIDENCE_CANARY_PAIRS = 5
MAX_FAILURE_RATE_REGRESSION = 0.10


def _config_key(config: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not config:
        return None
    return (
        str(config.get("model_id", "")),
        str(config.get("effort", "")),
        str(config.get("execution_mode", "")),
    )


def _find_configuration(category: dict[str, Any], config: dict[str, Any] | None) -> dict[str, Any] | None:
    key = _config_key(config)
    if key is None:
        return None
    for row in category.get("configurations", []):
        if _config_key(row) == key:
            return row
    return None


def _additional_pairs(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
    confidence: dict[str, Any],
) -> int:
    current_observations = int((current or {}).get("observations", 0))
    candidate_observations = int(candidate.get("observations", 0))
    current_paired = int((current or {}).get("paired_comparable_tasks", 0))
    candidate_paired = int(candidate.get("paired_comparable_tasks", 0))

    quality_gap = max(
        0,
        EXACT_FEEDBACK_MIN - min(current_observations, candidate_observations),
    )
    efficiency_gap = max(
        0,
        PAIRED_EFFICIENCY_MIN - min(current_paired, candidate_paired),
    )
    confidence_floor = MIN_FRESH_CANARY_PAIRS if confidence.get("level") == "high" else MEDIUM_CONFIDENCE_CANARY_PAIRS
    return max(MIN_FRESH_CANARY_PAIRS, confidence_floor, quality_gap, efficiency_gap)


def _inactive_plan(proposal: dict[str, Any]) -> dict[str, Any]:
    action = proposal["action"]
    if action == "collect_more":
        state = "collect_more"
        reason = "Routing evidence is not promotion-ready; collect controlled evidence before a canary."
    elif action == "keep":
        state = "no_change"
        reason = "No routing change is currently justified, so no promotion canary is required."
    else:
        state = "insufficient_evidence"
        reason = "There is not enough controlled evidence to define a safe promotion canary."
    return {
        "category": proposal["category"],
        "state": state,
        "current": proposal.get("current"),
        "candidate": proposal.get("candidate"),
        "recommended_paired_trials": 0,
        "reason": reason,
        "evidence_gaps": [],
        "acceptance_criteria": [],
        "rollback_criteria": [],
        "safe_to_apply": False,
        "requires_human_approval": False,
    }


def build_promotion_plans(
    proposals_report: dict[str, Any],
    leaderboard: dict[str, Any],
) -> dict[str, Any]:
    """Build review-only canary plans for empirical routing change proposals.

    The plan is intentionally non-executable. It defines the minimum fresh,
    matched validation work and the evidence rules that must still hold before
    an operator can consider a separate router-policy edit.
    """
    categories = {row["category"]: row for row in leaderboard.get("categories", [])}
    plans: list[dict[str, Any]] = []

    for proposal in proposals_report.get("proposals", []):
        if proposal["action"] != "propose_change":
            plans.append(_inactive_plan(proposal))
            continue

        category = categories.get(proposal["category"])
        candidate = proposal.get("candidate")
        if category is None or candidate is None:
            plans.append(_inactive_plan({**proposal, "action": "insufficient_evidence"}))
            continue

        current_evidence = _find_configuration(category, proposal.get("current"))
        candidate_evidence = _find_configuration(category, candidate) or candidate
        pairs = _additional_pairs(
            current_evidence,
            candidate_evidence,
            proposal.get("confidence", {}),
        )

        gaps: list[str] = []
        if current_evidence is None:
            gaps.append("The current router configuration has no exact controlled leaderboard row; fresh canary pairs must establish its baseline.")
        else:
            if int(current_evidence.get("observations", 0)) < EXACT_FEEDBACK_MIN:
                gaps.append("Current route needs more exact controlled outcome observations.")
            if int(current_evidence.get("paired_comparable_tasks", 0)) < PAIRED_EFFICIENCY_MIN:
                gaps.append("Current route needs more matched successful task IDs for efficiency evidence.")
        if int(candidate_evidence.get("paired_comparable_tasks", 0)) < PAIRED_EFFICIENCY_MIN:
            gaps.append("Candidate needs more matched successful task IDs for efficiency evidence.")

        plans.append(
            {
                "category": proposal["category"],
                "state": "ready_for_canary",
                "current": proposal["current"],
                "candidate": candidate,
                "current_evidence": current_evidence,
                "candidate_evidence": candidate_evidence,
                "recommended_paired_trials": pairs,
                "reason": (
                    "A different configuration is empirically promoted. Validate it on fresh, "
                    "matched tasks against the exact current route before any policy edit."
                ),
                "evidence_gaps": gaps,
                "acceptance_criteria": [
                    (
                        "Run both current and candidate on the same fresh task IDs for at least "
                        f"{pairs} paired trials; do not mix unmatched workloads."
                    ),
                    ("After adding the canary evidence, the category leaderboard must still return `promote` with the same candidate as winner."),
                    (
                        f"Promotion must still clear quality margin >= {QUALITY_PROMOTION_MARGIN:.2f}, "
                        "or clear the efficiency path with both sides efficiency-ready, empirical "
                        f"score margin >= {PROMOTION_SCORE_MARGIN:.2f}, and quality regression no "
                        f"worse than {MAX_QUALITY_REGRESSION_FOR_EFFICIENCY_PROMOTION:.2f}."
                    ),
                    "Any deterministic task judge or required validation gate must pass for the candidate.",
                ],
                "rollback_criteria": [
                    "Stop the canary on any deterministic judge or required validation failure attributable to the candidate.",
                    (
                        "Stop if the candidate failure rate exceeds the current route by more than "
                        f"{MAX_FAILURE_RATE_REGRESSION:.0%} after at least {MIN_FRESH_CANARY_PAIRS} matched pairs."
                    ),
                    "Do not promote if the post-canary leaderboard changes to hold, collect_more, or insufficient_evidence.",
                    "Do not promote if the post-canary empirical winner is no longer the proposed candidate.",
                ],
                "safe_to_apply": False,
                "requires_human_approval": True,
            }
        )

    counts = {
        state: sum(1 for plan in plans if plan["state"] == state)
        for state in ("ready_for_canary", "collect_more", "no_change", "insufficient_evidence")
    }
    return {
        "records": leaderboard.get("records", 0),
        "plans": plans,
        "state_counts": counts,
        "automatic_policy_mutation": False,
        "automatic_canary_execution": False,
    }


def promotion_plans_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Promotion / Canary Plans",
        "",
        "These plans are review-only. They neither execute canaries nor edit router policy.",
        "",
        "| Category | State | Current | Candidate | Fresh paired trials |",
        "|---|---|---|---|---:|",
    ]
    for plan in report["plans"]:
        current = plan.get("current") or {}
        candidate = plan.get("candidate") or {}
        current_label = "—"
        candidate_label = "—"
        if current:
            current_label = f"`{current.get('model_id')} / {current.get('effort')} / {current.get('execution_mode')}`"
        if candidate:
            candidate_label = f"`{candidate.get('model_id')} / {candidate.get('effort')} / {candidate.get('execution_mode')}`"
        lines.append(f"| {plan['category']} | **{plan['state']}** | {current_label} | {candidate_label} | {plan['recommended_paired_trials']} |")

    for plan in report["plans"]:
        if plan["state"] != "ready_for_canary":
            continue
        lines.extend(["", f"## {plan['category']}", "", plan["reason"], ""])
        if plan["evidence_gaps"]:
            lines.append("### Evidence gaps")
            lines.append("")
            lines.extend(f"- {item}" for item in plan["evidence_gaps"])
            lines.append("")
        lines.append("### Acceptance criteria")
        lines.append("")
        lines.extend(f"- {item}" for item in plan["acceptance_criteria"])
        lines.extend(["", "### Rollback / stop criteria", ""])
        lines.extend(f"- {item}" for item in plan["rollback_criteria"])

    lines.extend(
        [
            "",
            "`safe_to_apply` is always `false`. Passing a canary only makes a routing edit eligible for separate human review.",
            "",
        ]
    )
    return "\n".join(lines)


def write_promotion_outputs(
    report: dict[str, Any],
    output: str | None,
    json_output: str | None,
) -> str:
    markdown = promotion_plans_markdown(report)
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
    if json_output:
        target = Path(json_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return markdown
