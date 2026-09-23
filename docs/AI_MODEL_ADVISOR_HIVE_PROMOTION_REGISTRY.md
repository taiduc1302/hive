# AI Model Advisor: Hive Promotion Registry

AI Model Advisor v0.30 adds an operational registry over checkpoint-verified Hive promotion journals.

The registry does not replace journals or checkpoints. It aggregates them into a route-level view while preserving the existing evidence boundary:

promotion review -> preview -> verified apply/rollback -> journal -> independent checkpoint -> registry

## What the registry reports

For each category and Hive route (queen and/or worker), the registry can report:

- the checkpoint-verified current configuration;
- the active applied change ID, when it can be identified unambiguously;
- the verified rollback target for that active change;
- pending reviewed previews that are not yet verified as applied;
- all journal/checkpoint evidence used for the route;
- blockers when the evidence is inconsistent.

A scope of both contributes evidence to both the queen and worker routes.

## Checkpoint requirement

The registry accepts journal/checkpoint pairs.

A journal only contributes verified operational state when:

1. the journal passes the v0.28 internal hash-chain validation; and
2. the supplied v0.29 checkpoint exactly verifies that journal snapshot.

A valid journal with a mismatched checkpoint is blocked and is not treated as current evidence.

## Multiple snapshots for one change

The registry may receive multiple independently checkpointed snapshots for the same change ID, for example:

previewed -> applied_verified -> rolled_back_verified

It only collapses them to the most advanced snapshot when every older journal is an exact prefix of the selected journal and the category, scope, and reviewed transition are identical.

It blocks:

- divergent journals at the same entry depth;
- reuse of one change ID for different category/scope/transition metadata;
- a supposedly older snapshot that is not an exact prefix of the most advanced one.

This prevents the registry from inventing chronology from filenames, input order, or timestamps.

## Current-state conflicts

The registry deliberately does not infer ordering between different change IDs.

If two checkpoint-verified promotions claim different current configurations for the same category and Hive route, the route becomes:

blocked_conflicting_current_state

No current model is selected.

Likewise, if more than one applied_verified change claims the same current configuration, the registry does not guess which promotion is active. The rollback target becomes ambiguous and the route is blocked.

## Pending previews

A preview-only journal is visible as pending evidence but does not establish a current verified configuration or rollback target.

This prevents reviewed intent from being confused with verified runtime state.

## CLI

One verified promotion snapshot:

    python -m tools.ai_model_advisor.cli hive-promotion-registry \
      --pair hive-promotion-journal-applied.json \
        hive-promotion-checkpoint-applied.json \
      --require-ready \
      --output hive-promotion-registry.md \
      --json-output hive-promotion-registry.json

Multiple snapshots:

    python -m tools.ai_model_advisor.cli hive-promotion-registry \
      --pair coding-journal.json coding-checkpoint.json \
      --pair research-journal.json research-checkpoint.json \
      --json-output hive-promotion-registry.json

The standalone module exposes the same pair-oriented interface:

    python -m tools.ai_model_advisor.hive_promotion_registry \
      --pair coding-journal.json coding-checkpoint.json

When --require-ready is supplied, any registry blocker returns exit code 2.

## Registry status

Top-level status is:

- ready: all supplied usable evidence is internally consistent;
- blocked: at least one checkpoint mismatch, divergent snapshot history, conflicting current state, or ambiguous active promotion exists.

Route-level status may be:

- no_evidence;
- pending_only;
- current_verified;
- blocked_conflicting_current_state;
- blocked_ambiguous_active_promotion.

## Safety boundary

The registry is evidence-only.

It does not:

- edit Hive configuration;
- apply or reapply a model promotion;
- execute rollback;
- mutate routing policy;
- call model providers;
- rank conflicting evidence to choose a winner.

It keeps:

- safe_to_auto_apply: false;
- automatic_config_mutation: false;
- automatic_rollback: false;
- requires_human_approval: true.

The registry cannot establish real-world chronology between independent change IDs unless that ordering is explicitly proven by a future evidence layer. v0.30 therefore blocks inconsistent claims instead of guessing.
