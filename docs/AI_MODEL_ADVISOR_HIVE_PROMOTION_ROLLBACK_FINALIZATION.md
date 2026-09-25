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
