# AI Model Advisor: Hive Promotion Lifecycle Audit

AI Model Advisor v0.25 adds a read-only lifecycle auditor for the manual Hive promotion path.

The existing stages remain separate and independently useful:

1. fresh canary evidence;
2. promotion review;
3. non-mutating Hive promotion preview;
4. Hive runtime gate;
5. manual operator edit;
6. post-application verification receipt.

The lifecycle auditor does not replace those stages. It verifies that their artifacts belong to the same reviewed transition and reports the current operational state in one place.

## Evidence chain

v0.25 links the artifacts as follows:

```text
promotion review
      |
      | SHA-256 recorded by preview
      v
promotion preview
      |
      | SHA-256 recorded by runtime gate
      +------------------------------+
      |                              |
      v                              v
runtime gate                  verification receipt
```

The auditor recomputes the canonical SHA-256 of every supplied artifact and fails closed when the chain does not match.

It also requires exact agreement on:

- category;
- change ID;
- Hive scope (`queen`, `worker`, or `both`);
- before configuration;
- after configuration;
- rollback configuration;
- runtime-gate target configuration;
- receipt target configuration.

## Lifecycle states

The auditor emits one of these operational states:

- `blocked_invalid_review` — the selected promotion review is not a valid manual-review package;
- `blocked_invalid_preview` — the Hive preview is malformed or violates the non-mutating contract;
- `blocked_chain_mismatch` — hashes, IDs, scope, or exact transition data do not belong to the same promotion chain;
- `blocked_preview` — the preview itself is not ready for a manual Hive edit;
- `awaiting_runtime_gate` — review and preview are linked, but current runtime proof has not been supplied;
- `blocked_runtime_gate` — the linked runtime gate is present but did not prove runtime readiness;
- `ready_for_manual_hive_edit` — runtime proof is ready and the operator may perform the reviewed edit manually;
- `blocked_post_apply_drift` — the receipt shows that the current Hive config matches neither the complete approved before state nor the complete approved after state;
- `blocked_receipt_state` — the receipt is linked but has an unsupported state;
- `applied_verified` — the complete evidence chain is linked and the manually applied Hive config exactly matches the reviewed target.

A `not_applied` receipt keeps the lifecycle in `ready_for_manual_hive_edit`; it proves the operator has not yet made the reviewed change.

## CLI

Standalone:

```bash
python -m tools.ai_model_advisor.hive_promotion_lifecycle \
  --promotion-review promotion-review.json \
  --promotion-preview hive-promotion-preview.json \
  --runtime-gate hive-promotion-gate.json \
  --promotion-receipt hive-promotion-receipt.json \
  --json
```

Unified Advisor CLI:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-lifecycle \
  --promotion-review promotion-review.json \
  --promotion-preview hive-promotion-preview.json \
  --runtime-gate hive-promotion-gate.json \
  --promotion-receipt hive-promotion-receipt.json \
  --json-output model-advisor-output/hive-promotion-lifecycle.json \
  --output model-advisor-output/hive-promotion-lifecycle.md
```

`--runtime-gate` and `--promotion-receipt` are optional so the same command can be used while a promotion advances through the lifecycle.

Use `--require-applied-verified` when an automation or CI step should exit with code 2 unless the final state is exactly `applied_verified`.

## Safety boundary

The lifecycle auditor is verification only.

It does not:

- edit `configuration.json`;
- edit routing policy;
- apply a rollback;
- create provider credentials;
- infer that a runtime control works from registry metadata;
- treat a provider response as benchmark success.

The report keeps:

- `safe_to_auto_apply: false`;
- `automatic_config_mutation: false`;
- `automatic_rollback: false`;
- `requires_human_approval: true`.

Artifact hashes and non-secret promotion metadata are emitted. Provider credentials, API keys, API bases, and model response text are not copied into the lifecycle report.
