# AI Model Advisor Promotion / Canary Plans

AI Model Advisor v0.17 adds a review-only validation layer between a routing proposal and any manual router-policy edit.

The intended flow is:

1. `feedback-leaderboard` determines whether controlled evidence supports `promote`, `hold`, `collect_more`, or `insufficient_evidence`.
2. `routing-proposals` compares that empirical result with the current router primary.
3. `promotion-plan` converts only `propose_change` cases into a fresh matched-canary plan.
4. An operator runs the required controlled trials separately and records them in a dedicated canary feedback JSONL file.
5. v0.18 `canary_evaluate` evaluates only that fresh matched canary evidence and returns `eligible_for_manual_promotion`, `continue_canary`, or `rollback_candidate`.
6. Only `eligible_for_manual_promotion` makes the change eligible for further review.\n7. v0.20 `promotion-review` re-reads the current routing matrix, blocks stale route drift, and emits an exact before/after/rollback package for a separate human edit.

## Command

```bash
python -m tools.ai_model_advisor.cli promotion-plan \
  --routing-matrix model-advisor-output/routing-matrix.json \
  --feedback feedback.jsonl \
  --output model-advisor-output/promotion-plan.md \
  --json-output model-advisor-output/promotion-plan.json
```

A standalone module entry point is also available as `python -m tools.ai_model_advisor.promotion_plan_cli`.

## What a plan contains

For each category, the report records:

- the exact current router configuration;
- the proposed empirical candidate;
- whether a canary is required;
- the recommended number of fresh paired trials;
- evidence gaps on the current and candidate configurations;
- acceptance criteria;
- rollback / stop criteria;
- explicit `safe_to_apply: false` and `requires_human_approval` state.

A key safeguard is that the current route is looked up as an exact `model + effort + execution_mode` configuration. The planner does not assume that the current route is the empirical runner-up.

## Fresh matched trials

A canary always requires fresh matched task IDs even when historical evidence is already strong. Medium-confidence proposals use a larger default canary floor than high-confidence proposals. Existing exact-quality and paired-efficiency evidence can increase the required sample when one side is under the evidence thresholds.

The canary compares current and candidate on the same workload. Unmatched tasks must not be used to claim a promotion.

For evaluation, store fresh canary attempts separately from historical feedback. The v0.18 evaluator deliberately consumes a dedicated canary-feedback file so historical records cannot be mistaken for fresh validation.

## Acceptance contract

After canary feedback is recorded, the fresh-only category leaderboard must return `promote` with the same candidate as winner. The candidate must still satisfy the existing conservative Advisor promotion rule: either the quality margin clears the quality threshold, or both sides have paired efficiency evidence and the total empirical margin clears the efficiency-promotion threshold without a material quality regression.

Any deterministic judge or required validation gate must also pass.

## Stop / rollback contract

The plan instructs the operator to stop if a deterministic validation fails, if the candidate failure rate materially regresses against the current route after enough matched pairs, if the fresh canary leaderboard no longer returns `promote`, or if the empirical winner changes.

The v0.18 evaluator can return `rollback_candidate`, but it does not execute rollback automatically.

## Safety boundary

The planner never runs providers, never launches canary traffic, never edits routing policy, and never marks a plan safe for automatic application. Passing a canary only makes a routing change eligible for separate human review.
\n## v0.20 promotion review\n\nA successful canary is not treated as permanent authorization. Run `promotion-review` against a freshly generated routing matrix before editing any operator-owned routing layer. If the live primary no longer matches the exact pre-canary current configuration, the result is `blocked_route_drift` and the canary must be re-planned. Even `ready_for_manual_edit` keeps `safe_to_apply: false` and contains only a review manifest, never an automatic policy mutation.\n