# AI Model Advisor Canary Evaluation

AI Model Advisor v0.18 added the review-only promotion-canary evaluator. v0.19 hardens its evidence matching so duplicate attempts cannot silently become promotion evidence.

The evaluator consumes two explicit inputs:

1. a `promotion-plan.json` produced by v0.17; and
2. a separate JSONL file containing only fresh canary feedback.

Keeping canary feedback separate from historical feedback is intentional. It prevents old observations from being misrepresented as fresh validation evidence.

## Command

```bash
python -m tools.ai_model_advisor.canary_evaluate \
  --promotion-plan model-advisor-output/promotion-plan.json \
  --canary-feedback canary-feedback.jsonl \
  --output model-advisor-output/canary-evaluation.md \
  --json-output model-advisor-output/canary-evaluation.json
```

## Decisions

For an active `ready_for_canary` plan, the evaluator returns exactly one of:

- `eligible_for_manual_promotion`: enough fresh matched task pairs exist and the fresh canary evidence independently promotes the same candidate;
- `continue_canary`: the sample is incomplete or fresh matched evidence does not yet re-confirm the candidate;
- `rollback_candidate`: the candidate failure rate materially regresses against the current route after enough matched pairs.

Even `eligible_for_manual_promotion` keeps `safe_to_apply: false`. It means only that a separate human-reviewed routing edit may now be considered.

## Matched-task requirement

Only task IDs present for both the exact current configuration and exact candidate configuration count as canary pairs. Unmatched tasks are excluded from the decision.

The exact configuration identity is:

`model_id + effort + execution_mode`

### Duplicate-attempt integrity

A task ID is also excluded when either exact configuration has more than one record for that same task ID. The evaluator treats that task as **ambiguous** rather than guessing which retry or re-run should represent the canary result.

This mirrors the controlled experiment evaluator: duplicate attempts are evidence-quality problems, not extra votes. JSON and Markdown outputs report both ambiguous duplicate task IDs and incomplete one-sided task IDs so the operator can repair the canary dataset before making a promotion decision.

## Independent fresh-evidence check

The evaluator builds a temporary empirical leaderboard from the matched canary records only. Eligibility requires that this fresh-only leaderboard returns `promote` and that its winner is still the candidate from the promotion plan.

This is deliberately stricter than simply reusing the historical leaderboard that created the proposal.

## Rollback guardrail

After at least three matched pairs, the evaluator returns `rollback_candidate` when the candidate failure rate exceeds the current route by more than 10 percentage points.

The evaluator does not execute a rollback. `automatic_rollback` remains `false`; the result is a decision-support signal for an operator.

## Safety boundary

The evaluator:

- makes no provider calls;
- runs no canary traffic;
- edits no routing policy;
- executes no rollback;
- never sets `safe_to_apply` to `true`.
