# AI Model Advisor: Hive Promotion Rollback Audit

AI Model Advisor v0.27 extends the v0.26 manual rollback proof with replay-safe receipt chaining.

A single post-rollback receipt that says `not_applied` is not enough to prove a rollback happened. The same state also describes a promotion that was never applied.

v0.26 therefore requires two independent facts:

1. a prior Hive promotion lifecycle artifact with `state=applied_verified`;
2. a new promotion receipt linked to the same reviewed preview that proves every affected Hive section now exactly matches the approved before/rollback configuration.

Only that sequence becomes `rolled_back_verified`.

v0.27 adds one more requirement: the rollback receipt must carry
`previous_receipt_sha256` equal to the exact applied promotion receipt hash recorded
by the prior `applied_verified` lifecycle. This prevents an older pre-application
`not_applied` receipt from being replayed later as false rollback evidence.

## Evidence sequence

```text
reviewed promotion preview
          |
          v
applied_verified lifecycle
          |
          | proves reviewed after-state was applied
          v
manual rollback by operator
          |
          v
new verification receipt
          |
          | must prove exact reviewed before-state
          v
rolled_back_verified
```

The rollback auditor verifies the same:

- preview SHA-256;
- category;
- change ID;
- Hive scope;
- before configuration;
- after configuration;
- rollback configuration.

It also requires the prior lifecycle to identify the applied promotion receipt that originally established `applied_verified`.

## Rollback states

- `rolled_back_verified` — prior application was proven and the new receipt proves the complete reviewed before-state has been restored;
- `rollback_not_applied` — the new receipt still proves the approved after-state;
- `blocked_rollback_drift` — the new receipt matches neither the full before-state nor the full after-state;
- `blocked_invalid_applied_lifecycle` — no valid prior `applied_verified` lifecycle exists;
- `blocked_stale_rollback_receipt` — rollback evidence is not hash-linked to the exact applied receipt recorded by the verified lifecycle;
- `blocked_chain_mismatch` — preview/lifecycle/receipt hashes, IDs, scope, or exact transition data do not belong to the same promotion;
- `blocked_invalid_preview` — the reviewed Hive preview is malformed or violates the non-mutating contract;
- `blocked_rollback_receipt_state` — the linked receipt has an unsupported state.

## CLI

Standalone:

```bash
python -m tools.ai_model_advisor.hive_promotion_rollback \
  --promotion-preview hive-promotion-preview.json \
  --applied-lifecycle hive-promotion-lifecycle.json \
  --rollback-receipt hive-promotion-rollback-receipt.json \
  --json
```

Unified Advisor CLI:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-receipt \
  --promotion-preview hive-promotion-preview.json \
  --hive-config hive-config-after-manual-rollback.json \
  --previous-receipt hive-promotion-applied-receipt.json \
  --json-output hive-promotion-rollback-receipt.json

python -m tools.ai_model_advisor.cli hive-promotion-rollback \
  --promotion-preview hive-promotion-preview.json \
  --applied-lifecycle hive-promotion-lifecycle.json \
  --rollback-receipt hive-promotion-rollback-receipt.json \
  --output model-advisor-output/hive-promotion-rollback.md \
  --json-output model-advisor-output/hive-promotion-rollback.json \
  --require-rolled-back-verified
```

The verification receipt itself is generated with the existing read-only `hive-promotion-receipt` command after the operator performs the rollback.

## Safety boundary

The rollback auditor never performs a rollback.

It does not:

- edit Hive configuration;
- edit routing policy;
- execute the rollback patch;
- restore credentials;
- auto-apply another model;
- infer rollback success from operator intent.

Every report keeps:

- `safe_to_auto_apply: false`;
- `automatic_config_mutation: false`;
- `automatic_rollback: false`;
- `requires_human_approval: true`.

Provider credentials, API keys, API bases, and response text are not copied into the rollback report.
