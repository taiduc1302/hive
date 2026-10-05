# AI Model Advisor — Hive Rollback Finalization

AI Model Advisor v0.35 closes the evidence-chain gap between the v0.34 rollback freshness preflight and the existing post-edit rollback audit.

The finalization step is **verification-only**. It never edits Hive configuration and it never performs automatic rollback.

## Why this exists

Before v0.35, Hive could separately prove:

1. the rollback plan was still fresh immediately before a manual edit; and
2. the final Hive configuration returned exactly to the reviewed rollback target.

Those facts were not bound into one artifact.

v0.35 adds a final attestation that accepts the exact:

- promotion preview;
- applied lifecycle;
- rollback plan;
- successful rollback preflight;
- rollback receipt.

It validates the rollback-plan self-hash, validates the preflight self-hash and plan linkage, runs the existing replay-safe rollback audit, and emits one hash-linked finalization artifact.

## CLI

```bash
python -m tools.ai_model_advisor.cli hive-promotion-rollback-finalize \
  --promotion-preview promotion-preview.json \
  --applied-lifecycle applied-lifecycle.json \
  --rollback-plan rollback-plan.json \
  --rollback-preflight rollback-preflight.json \
  --rollback-receipt rollback-receipt.json \
  --output rollback-finalization.md \
  --json-output rollback-finalization.json \
  --require-verified
```

A successful artifact reports:

```
state = rollback_verified_from_fresh_preflight
rollback_verified_from_fresh_preflight = true
```

If the existing rollback audit does not verify the final state, the finalizer returns `blocked_rollback_audit`. Malformed, stale, or tampered preflight evidence fails closed.

## Evidence chain

The final artifact records canonical SHA-256 values for:

- promotion preview;
- applied lifecycle;
- rollback plan;
- rollback preflight;
- rollback receipt;
- rollback audit;
- finalization artifact itself.

This is integrity evidence, not a trusted timestamp or digital signature. It proves consistency among supplied artifacts; it does not prove wall-clock ordering beyond the semantics of the verified chain.

## Safety boundary

v0.35 preserves the existing operator boundary:

- `safe_to_auto_apply = false`;
- automatic config mutation disabled;
- automatic rollback disabled;
- human approval required;
- no provider calls;
- no credentials or unrelated Hive configuration copied into the finalization artifact.


## v0.37: append finalization to the promotion journal

v0.37 makes the stronger v0.35 finalization evidence part of the append-only promotion history.

A successful finalization now also carries the reviewed promotion identity:

- `category`;
- `change_id`;
- `scope`;
- exact reviewed `transition`.

That lets the journal fail closed if a finalization artifact belongs to a different promotion, even when individual evidence hashes are otherwise well-formed.

After a verified `rollback_audit`, append the finalization artifact:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-journal append \
  --journal hive-promotion-journal-rolled-back.json \
  --event rollback_finalization \
  --artifact rollback-finalization.json \
  --output hive-promotion-journal-finalized.md \
  --json-output hive-promotion-journal-finalized.json
```

The journal requires the finalization's `rollback_audit_sha256` and `applied_lifecycle_sha256` to match the exact artifacts already recorded in the hash chain. A successful append produces terminal state `rolled_back_finalized`.

Existing journals ending at `rolled_back_verified` remain valid and continue to work with checkpoints, the registry, reconciliation, and status reporting.

## v0.37 stacked validation

The v0.37 branch is stacked on AI Model Advisor v0.36 after the controlled Hive AgentLoop target was synchronized into this branch. The rollback-finalization/journal contract is unchanged by that sync: v0.37 only extends promotion-history integrity, while v0.36 remains the execution-target layer.
