# AI Model Advisor: Hive Promotion Journal

AI Model Advisor v0.28 adds an append-only evidence journal for one reviewed Hive model promotion.

The journal does not replace the existing promotion artifacts. It links them into one integrity-checked lifecycle:

```text
promotion preview
      |
      v
previewed journal
      |
      | append applied_verified lifecycle
      v
applied_verified journal
      |
      | append rolled_back_verified rollback audit
      v
rolled_back_verified journal
```

## Why this exists

Before v0.28, the promotion preview, lifecycle audit, verification receipts, and rollback audit were individually verifiable but still existed as separate JSON artifacts.

The journal adds a small append-only chain so an operator can answer:

- which reviewed promotion this history belongs to;
- whether the promotion was only previewed, applied, or later rolled back;
- whether any earlier journal entry was changed after later evidence was appended;
- whether a rollback audit belongs to the exact applied lifecycle already recorded in the journal.

Each journal entry includes:

- a zero-based sequence;
- event type;
- resulting lifecycle state;
- SHA-256 of the full source artifact;
- SHA-256 of the previous journal entry;
- selected supporting evidence hashes;
- a canonical SHA-256 of the complete journal entry.

Changing an earlier entry breaks the chain.

## Supported event order

v0.28 intentionally supports only:

```text
promotion_preview
promotion_preview -> applied_lifecycle
promotion_preview -> applied_lifecycle -> rollback_audit
```

It fails closed if an operator attempts to:

- append rollback before a verified application;
- append a second application to the same journal;
- append unsupported event types;
- use an artifact from a different category, change ID, scope, preview, or transition;
- tamper with an existing entry or journal head.

## Unified CLI

Create the journal from a reviewed Hive promotion preview:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-journal init \
  --promotion-preview hive-promotion-preview.json \
  --output hive-promotion-journal.md \
  --json-output hive-promotion-journal.json
```

Append a verified application:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-journal append \
  --journal hive-promotion-journal.json \
  --event applied_lifecycle \
  --artifact hive-promotion-lifecycle.json \
  --output hive-promotion-journal-applied.md \
  --json-output hive-promotion-journal-applied.json
```

Append a verified rollback:

```bash
python -m tools.ai_model_advisor.cli hive-promotion-journal append \
  --journal hive-promotion-journal-applied.json \
  --event rollback_audit \
  --artifact hive-promotion-rollback.json \
  --output hive-promotion-journal-rolled-back.md \
  --json-output hive-promotion-journal-rolled-back.json
```

## Safety boundary

The journal is evidence-only.

It does not:

- edit Hive configuration;
- apply a model promotion;
- perform a rollback;
- change routing policy;
- store provider credentials;
- copy model response text.

Every journal keeps:

- `safe_to_auto_apply: false`;
- `automatic_config_mutation: false`;
- `automatic_rollback: false`;
- `requires_human_approval: true`.

The source artifacts remain the authority for the actual reviewed transition and verification evidence. The journal provides lifecycle continuity and tamper detection across those artifacts.
