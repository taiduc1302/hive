# AI Model Advisor Promotion Review Package

AI Model Advisor v0.20 adds a fail-closed review step between a successful fresh canary and any manual routing edit.

The flow is now:

1. controlled evidence builds an empirical leaderboard;
2. routing proposals identify a candidate;
3. a promotion plan defines fresh matched canary work;
4. the canary evaluator decides whether fresh evidence is eligible;
5. promotion review re-reads the current routing matrix and refuses stale promotions;
6. only a ready_for_manual_edit package can be considered by a human operator.

## Command

    python -m tools.ai_model_advisor.cli promotion-review \
      --canary-evaluation model-advisor-output/canary-evaluation.json \
      --routing-matrix model-advisor-output/routing-matrix.json \
      --output model-advisor-output/promotion-review.md \
      --json-output model-advisor-output/promotion-review.json

A standalone module entry point is also available:

    python -m tools.ai_model_advisor.promotion_review ...

## Route-drift guard

A canary is created against an exact current configuration:

    model_id + effort + execution_mode

Before producing a manual change package, v0.20 compares that pre-canary configuration with the current routing-matrix primary.

If they differ, the review state becomes blocked_route_drift. The old canary is not treated as authorization to promote against a route that has already changed. The operator must rebuild the plan and run fresh validation.

## Defense in depth

Even when the input state says eligible_for_manual_promotion, the review step requires all of the following:

- safe_to_apply is still false;
- human approval is required;
- matched canary pairs meet the plan minimum;
- the fresh-only leaderboard still says promote;
- the fresh leaderboard winner still matches the candidate;
- current and candidate configurations are complete;
- the live routing primary still exactly matches the pre-canary current configuration;
- candidate and current are not identical.

Any inconsistent input is blocked rather than converted into a change package.

## Manual change package

A ready review includes:

- stable change ID;
- exact before configuration;
- exact after candidate;
- explicit rollback_to configuration;
- matched / required canary counts;
- a human review checklist.

The package intentionally does not invent a file path for production routing. The current Advisor routing matrix is generated output, while the actual routing control may live in a host-specific or operator-owned layer. v0.20 describes the exact configuration transition without pretending it can safely mutate an unknown target.

## Safety boundary

The review command:

- makes zero provider calls;
- writes no routing policy;
- performs no automatic rollback;
- always reports safe_to_apply: false;
- always reports automatic_policy_mutation: false;
- requires a separate explicit human edit and post-edit validation.
