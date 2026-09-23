# AI Model Advisor: Hive Promotion Journal Checkpoints

AI Model Advisor v0.29 adds an independently retained checkpoint for a Hive promotion journal.

## Why a checkpoint exists

The v0.28 promotion journal validates its own append-only SHA-256 chain. That proves that the entries inside the journal are internally consistent, but an older valid prefix is also internally consistent. If the current journal were replaced with that older prefix, journal validation alone would not prove that a newer state had previously existed.

A checkpoint commits to one exact journal snapshot so a later verifier can detect:

- rollback to an older valid journal prefix;
- truncation of valid later entries;
- replacement with a different journal snapshot;
- advancement beyond the checkpointed snapshot.

This is exact-snapshot verification, not an "at least this state" check.

## Create a checkpoint

```bash
python -m tools.ai_model_advisor.hive_promotion_checkpoint create \
  --journal model-advisor-output/hive-promotion-journal.json \
  --output model-advisor-output/hive-promotion-checkpoint.md \
  --json-output model-advisor-output/hive-promotion-checkpoint.json
```

The JSON checkpoint records:

- category, change ID, scope, and lifecycle state;
- journal entry count;
- current journal head-entry SHA-256;
- canonical SHA-256 of the full journal;
- canonical SHA-256 of the reviewed transition;
- a canonical SHA-256 of the checkpoint itself.

## Verify a journal against the checkpoint

```bash
python -m tools.ai_model_advisor.hive_promotion_checkpoint verify \
  --journal model-advisor-output/hive-promotion-journal.json \
  --checkpoint model-advisor-output/hive-promotion-checkpoint.json \
  --output model-advisor-output/hive-promotion-checkpoint-verify.md \
  --json-output model-advisor-output/hive-promotion-checkpoint-verify.json
```

Exit status is:

- `0` when the journal exactly matches the checkpoint;
- `2` when the journal is valid but does not match the checkpoint;
- non-zero with an error when the journal or checkpoint is malformed.

The verification JSON reports every mismatched committed field.

## Evidence boundary

The checkpoint must be retained independently from the journal it protects. Storing both in the same mutable location does not prevent an attacker or accidental restore from replacing both with an older matching pair.

A checkpoint is a SHA-256 commitment. It is **not**:

- a digital signature;
- a trusted timestamp;
- proof of who created the journal;
- proof that a checkpoint has not itself been replaced when no independent copy exists.

A stronger deployment can publish or archive the checkpoint in a separately controlled system, release artifact, audit store, or other durable channel.

## Safety boundary

Checkpoint creation and verification are evidence-only operations.

They do not:

- edit Hive configuration;
- promote a model;
- change routing policy;
- apply rollback;
- make provider calls.

The existing manual approval and fail-closed promotion controls remain unchanged.
