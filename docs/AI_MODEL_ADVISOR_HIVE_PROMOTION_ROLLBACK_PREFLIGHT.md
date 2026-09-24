# AI Model Advisor: Hive Manual Rollback Preflight

AI Model Advisor v0.34 adds a final read-only freshness check immediately before a manual rollback edit.

It verifies that the current Hive configuration still matches the preconditions recorded in the v0.33 rollback plan.

## Why this exists

A rollback plan can be correct when it is generated and stale later.

Between plan generation and the operator's manual edit, another process or person may change:

- llm;
- worker_llm;
- provider;
- model;
- reasoning effort.

v0.34 detects that drift before exposing the rollback patch as ready for manual use.

## Verification

For every rollback-plan precondition, preflight:

1. reads the current Hive section;
2. sanitizes it to provider, model, reasoning effort, and key-presence metadata;
3. computes the canonical SHA-256;
4. compares it with expected_current_sha256 from the rollback plan.

The rollback plan's own rollback_plan_sha256 is also revalidated before any precondition check.

A modified/tampered rollback plan is rejected as malformed evidence.

## States

Preflight reports:

- ready_for_manual_edit when every current-section precondition still matches;
- blocked_stale_rollback_plan when one or more current sections changed.

When stale, merge_patch is empty.

## CLI

Unified CLI:

    python -m tools.ai_model_advisor.cli hive-promotion-rollback-preflight \
      --rollback-plan hive-promotion-rollback-plan.json \
      --hive-config hive-config.json \
      --require-ready \
      --output hive-promotion-rollback-preflight.md \
      --json-output hive-promotion-rollback-preflight.json

Standalone module:

    python -m tools.ai_model_advisor.hive_promotion_rollback_preflight ...

With --require-ready, stale plans return exit code 2.

## Privacy

The report emits only sanitized route observations and hashes.

It does not emit:

- API keys;
- API bases;
- unrelated Hive configuration;
- model response text.

## Safety boundary

v0.34 still does not perform rollback.

It keeps:

- safe_to_auto_apply: false;
- automatic_config_mutation: false;
- automatic_rollback: false;
- requires_human_approval: true.

A ready preflight proves only that the supplied current Hive config matched the rollback-plan preconditions at verification time. A later config change can still invalidate the plan.
