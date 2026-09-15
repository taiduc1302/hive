from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore, UsageRecord

MAX_FAILURE_RATE_REGRESSION = 0.10


def _config_key(config: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not config:
        return None
    return (
        str(config.get("model_id", "")),
        str(config.get("effort", "")),
        str(config.get("execution_mode", "")),
    )


def _record_key(record: UsageRecord) -> tuple[str, str, str]:
    return record.model_id, record.effort, record.execution_mode


def _matched_records(
    records: tuple[UsageRecord, ...],
    current: dict[str, Any],
    candidate: dict[str, Any],
    category: str,
) -> tuple[list[UsageRecord], list[UsageRecord], set[str]]:
    current_key = _config_key(current)
    candidate_key = _config_key(candidate)
    current_by_task: dict[str, UsageRecord] = {}
    candidate_by_task: dict[str, UsageRecord] = {}

    for record in records:
        if record.task_category != category or not record.task_id:
            continue
        if _record_key(record) == current_key:
            current_by_task[record.task_id] = record
        elif _record_key(record) == candidate_key:
            candidate_by_task[record.task_id] = record

    matched = set(current_by_task) & set(candidate_by_task)
    return (
        [current_by_task[task_id] for task_id in sorted(matched)],
        [candidate_by_task[task_id] for task_id in sorted(matched)],
        matched,
    )


def _failure_rate(records: list[UsageRecord]) -> float:
    if not records:
        return 0.0
    return sum(record.outcome == "failure" for record in records) / len(records)


def _build_matched_store(
    current_records: list[UsageRecord],
    candidate_records: list[UsageRecord],
) -> FeedbackStore:
    return FeedbackStore([*current_records, *candidate_records])


def _category_decision(leaderboard: dict[str, Any], category: str) -> dict[str, Any] | None:
    for item in leaderboard.get("categories", []):
        if item.get("category") == category:
            return item.get("decision")
    return None


def _evaluate_ready_plan(plan: dict[str, Any], store: FeedbackStore) -> dict[str, Any]:
    category = str(plan["category"])
    current = plan.get("current") or {}
    candidate = plan.get("candidate") or {}
    required_pairs = int(plan.get("recommended_paired_trials", 0))
    current_records, candidate_records, matched = _matched_records(
        store.records,
        current,
        candidate,
        category,
    )
    matched_pairs = len(matched)
    current_failure_rate = _failure_rate(current_records)
    candidate_failure_rate = _failure_rate(candidate_records)
    failure_regression = candidate_failure_rate - current_failure_rate

    matched_store = _build_matched_store(current_records, candidate_records)
    leaderboard = build_empirical_leaderboard(matched_store)
    decision = _category_decision(leaderboard, category)
    winner = (decision or {}).get("winner")
    winner_matches_candidate = _config_key(winner) == _config_key(candidate)

    if matched_pairs >= 3 and failure_regression > MAX_FAILURE_RATE_REGRESSION:
        state = "rollback_candidate"
        reason = (
            "Candidate failure rate materially regressed against the current route on matched canary tasks."
        )
    elif matched_pairs < required_pairs:
        state = "continue_canary"
        reason = (
            f"Only {matched_pairs} matched canary pairs are available; {required_pairs} are required by the plan."
        )
    elif not decision or decision.get("status") != "promote" or not winner_matches_candidate:
        state = "continue_canary"
        reason = (
            "The fresh matched canary evidence does not yet re-confirm the proposed candidate as an empirical promotion."
        )
    else:
        state = "eligible_for_manual_promotion"
        reason = (
            "The required fresh matched trials are complete and independently re-confirm the same candidate as promoted."
        )

    return {
        "category": category,
        "state": state,
        "current": current,
        "candidate": candidate,
        "required_pairs": required_pairs,
        "matched_pairs": matched_pairs,
        "current_failure_rate": round(current_failure_rate, 6),
        "candidate_failure_rate": round(candidate_failure_rate, 6),
        "failure_rate_regression": round(failure_regression, 6),
        "leaderboard_status": (decision or {}).get("status"),
        "leaderboard_winner": winner,
        "winner_matches_candidate": winner_matches_candidate,
        "reason": reason,
        "safe_to_apply": False,
        "requires_human_approval": state == "eligible_for_manual_promotion",
    }


def evaluate_promotion_canary(
    promotion_report: dict[str, Any],
    canary_feedback: FeedbackStore,
) -> dict[str, Any]:
    """Evaluate fresh matched canary evidence without mutating router policy."""
    evaluations: list[dict[str, Any]] = []
    for plan in promotion_report.get("plans", []):
        if plan.get("state") != "ready_for_canary":
            evaluations.append(
                {
                    "category": plan.get("category"),
                    "state": "continue_canary",
                    "current": plan.get("current"),
                    "candidate": plan.get("candidate"),
                    "required_pairs": int(plan.get("recommended_paired_trials", 0)),
                    "matched_pairs": 0,
                    "reason": "This category does not currently have an active promotion canary plan.",
                    "safe_to_apply": False,
                    "requires_human_approval": False,
                }
            )
            continue
        evaluations.append(_evaluate_ready_plan(plan, canary_feedback))

    states = ("eligible_for_manual_promotion", "continue_canary", "rollback_candidate")
    return {
        "records": len(canary_feedback.records),
        "evaluations": evaluations,
        "state_counts": {
            state: sum(item["state"] == state for item in evaluations)
            for state in states
        },
        "automatic_policy_mutation": False,
        "automatic_rollback": False,
    }


def canary_evaluation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Canary Evaluation",
        "",
        "Evaluation uses only the supplied fresh canary feedback file and exact matched task IDs.",
        "",
        "| Category | Decision | Matched / Required | Current failure | Candidate failure |",
        "|---|---|---:|---:|---:|",
    ]
    for item in report["evaluations"]:
        current_failure = item.get("current_failure_rate")
        candidate_failure = item.get("candidate_failure_rate")
        current_label = "—" if current_failure is None else f"{current_failure:.1%}"
        candidate_label = "—" if candidate_failure is None else f"{candidate_failure:.1%}"
        lines.append(
            f"| {item['category']} | **{item['state']}** | "
            f"{item.get('matched_pairs', 0)} / {item.get('required_pairs', 0)} | "
            f"{current_label} | {candidate_label} |"
        )
        lines.extend(["", f"- **{item['category']}**: {item['reason']}"])

    lines.extend(
        [
            "",
            "`safe_to_apply` remains `false` even when a candidate is eligible for manual promotion.",
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
    parser = argparse.ArgumentParser(description="Evaluate fresh matched routing-promotion canary feedback.")
    parser.add_argument("--promotion-plan", required=True)
    parser.add_argument("--canary-feedback", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    promotion = json.loads(Path(args.promotion_plan).read_text(encoding="utf-8"))
    if not isinstance(promotion, dict):
        raise ValueError("promotion plan root must be a JSON object")
    report = evaluate_promotion_canary(promotion, FeedbackStore.load(args.canary_feedback))
    markdown = canary_evaluation_markdown(report)
    _write(args.output, markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not args.output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
