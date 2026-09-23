# AI Model Advisor: Hive Promotion Registry Reconciliation

AI Model Advisor v0.31 adds a read-only reconciliation layer between the checkpoint-backed promotion registry and the current Hive configuration.

The evidence path is:

promotion evidence -> journal -> independent checkpoint -> registry -> current Hive config reconciliation

## Purpose

The v0.30 registry reports what checkpoint-verified promotion evidence says the current route should be.

v0.31 checks that claim against the Hive configuration currently supplied by the operator.

For each registry route it maps:

- queen -> llm
- worker -> worker_llm

and compares:

- provider;
- model;
- reasoning effort;
- whether provider-default effort is represented by an absent reasoning_effort key;
- execution mode support.

Static reconciliation currently verifies execution_mode=single only.

## Reconciliation states

Top-level status can be:

- verified: every current_verified registry route exactly matches Hive config;
- drifted: one or more verifiable routes differ;
- partial: no drift among verifiable routes, but at least one registry route lacks one current verified configuration;
- blocked_registry: the source registry itself is blocked and cannot be treated as authoritative current state.

Each route can be:

- verified;
- drifted;
- not_verifiable.

## Manual rollback readiness

A route is marked manual_rollback_ready only when all of the following are true:

1. the registry route has one current_verified configuration;
2. current Hive config exactly matches that configuration;
3. the registry identifies one unambiguous active change;
4. the registry has a verified rollback target for that change.

This flag is advisory evidence only. It does not execute rollback.

If current Hive config has drifted, manual_rollback_ready is false even when the registry contains a rollback target.

## CLI

Unified CLI:

    python -m tools.ai_model_advisor.cli hive-promotion-reconcile \
      --registry hive-promotion-registry.json \
      --hive-config hive-config.json \
      --require-verified \
      --output hive-promotion-reconciliation.md \
      --json-output hive-promotion-reconciliation.json

Standalone module:

    python -m tools.ai_model_advisor.hive_promotion_reconcile \
      --registry hive-promotion-registry.json \
      --hive-config hive-config.json

With --require-verified, any drift, partial state, or blocked registry returns exit code 2.

## Provider-default reasoning effort

When registry effort is default, reconciliation requires the reasoning_effort key to be absent from the Hive route.

A present key with null is treated as different from an absent key because the existing promotion receipt contract uses key removal to restore provider defaults.

## Privacy boundary

The reconciliation output includes only route-level observations needed for verification:

- provider;
- model;
- reasoning effort;
- whether reasoning_effort was explicitly present.

It does not emit:

- API keys;
- API bases;
- credentials;
- unrelated Hive configuration;
- model response text.

The route observation hash is computed only from the sanitized observations included in the report.

## Safety boundary

v0.31 remains verification-only.

It does not:

- edit Hive configuration;
- apply a promotion;
- execute rollback;
- change routing policy;
- call model providers.

Reports keep:

- safe_to_auto_apply: false;
- automatic_config_mutation: false;
- automatic_rollback: false;
- requires_human_approval: true.

The reconciliation result proves agreement only with the specific Hive configuration supplied to the command. It is not a trusted timestamp and does not prove that configuration remains unchanged afterward.
