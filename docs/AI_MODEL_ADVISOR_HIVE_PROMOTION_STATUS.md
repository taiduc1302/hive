# AI Model Advisor: Hive Promotion Operational Status

AI Model Advisor v0.32 adds one read-only operator status bundle over the existing promotion evidence stack.

The command composes:

promotion journals + checkpoints -> promotion registry -> live Hive reconciliation -> operator status

It does not introduce a new source of truth. Registry and reconciliation artifacts remain the underlying evidence layers.

## Operator statuses

The bundle reports one top-level status:

- verified: checkpoint-backed registry state matches the supplied Hive config and there are no pending promotions;
- attention: evidence is usable but operator review is required, such as pending promotions or unverified routes;
- drifted: current Hive config differs from checkpoint-backed registry state;
- blocked: registry evidence itself is inconsistent or checkpoint verification failed.

It also emits one operator_action value:

- none;
- review_pending_promotions;
- resolve_unverified_routes;
- investigate_live_config_drift;
- resolve_evidence_blockers;
- review_status_evidence.

## Route summary

For every category and Hive route, the report includes:

- registry status;
- live reconciliation state;
- current checkpoint-verified configuration;
- sanitized observed Hive route configuration;
- active change ID when unambiguous;
- rollback target;
- manual rollback readiness;
- pending promotions;
- drift differences.

## Rollback readiness

A route is counted as manual rollback ready only when the v0.31 reconciliation layer confirms the current Hive config exactly matches the active registry state and the registry provides one verified rollback target.

The status command never executes rollback.

## CLI

    python -m tools.ai_model_advisor.cli hive-promotion-status \
      --pair coding-journal.json coding-checkpoint.json \
      --pair research-journal.json research-checkpoint.json \
      --hive-config hive-config.json \
      --require-verified \
      --output hive-promotion-status.md \
      --json-output hive-promotion-status.json

The standalone module exposes the same pair-oriented interface:

    python -m tools.ai_model_advisor.hive_promotion_status ...

With --require-verified, any attention, drifted, or blocked state returns exit code 2.

## Evidence hashes

The bundle records:

- registry SHA-256;
- reconciliation SHA-256;
- sanitized route-observation SHA-256 when available;
- canonical status-bundle SHA-256.

These hashes support traceability but are not signatures or trusted timestamps.

## Privacy and safety

The bundle inherits the v0.31 sanitized route observations. It does not emit API keys, API bases, unrelated Hive configuration, or model response text.

It remains read-only:

- safe_to_auto_apply: false;
- automatic_config_mutation: false;
- automatic_rollback: false;
- requires_human_approval: true.

No model provider calls, routing-policy mutation, promotion application, or rollback execution are performed.
