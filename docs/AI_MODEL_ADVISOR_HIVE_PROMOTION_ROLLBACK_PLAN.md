# AI Model Advisor: Hive Manual Rollback Plan

AI Model Advisor v0.33 adds a non-mutating rollback plan generated from the v0.32 operational status bundle.

The plan does not execute rollback. It prepares an exact merge-patch preview and the preconditions an operator must re-check before making any manual edit.

## Eligibility

A rollback plan is only prepared from operational status that is:

- verified; or
- attention while the selected route is still manual_rollback_ready.

Drifted or blocked operational status is rejected.

At least one route must have manual_rollback_ready=true.

## Physical-section conflict handling

Hive queen and worker routes map to:

- queen -> llm
- worker -> worker_llm

Multiple evidence routes can theoretically point at the same physical Hive section.

v0.33 fails closed when more than one rollback-ready route maps to the same section with different active change IDs or rollback targets.

The plan never chooses one conflicting rollback claim automatically.

## Patch semantics

For each conflict-free rollback-ready section, the plan includes:

- active change ID;
- verified current configuration;
- rollback target;
- non-mutating merge-patch preview;
- SHA-256 precondition for the sanitized current section observation.

When rollback effort is default, the generated patch sets reasoning_effort to null so a JSON merge-patch removes the explicit override and returns the route to provider default behavior.

## CLI

Unified CLI:

    python -m tools.ai_model_advisor.cli hive-promotion-rollback-plan \
      --status hive-promotion-status.json \
      --require-ready \
      --output hive-promotion-rollback-plan.md \
      --json-output hive-promotion-rollback-plan.json

Standalone module:

    python -m tools.ai_model_advisor.hive_promotion_rollback_plan ...

With --require-ready, blocked plans return exit code 2.

## Preconditions

Before any manual rollback edit, the operator must confirm the current route still matches the precondition recorded by the plan.

The plan contains a canonical SHA-256 of the sanitized expected current section. It does not include credentials or unrelated Hive configuration.

A later config change invalidates the operational assumption even if the plan file itself remains unchanged.

## Safety boundary

v0.33 remains preview-only:

- safe_to_auto_apply: false;
- automatic_config_mutation: false;
- automatic_rollback: false;
- requires_human_approval: true.

It does not:

- write Hive config;
- restart Hive;
- call a model provider;
- execute rollback;
- choose between conflicting rollback claims.

The rollback patch is an operator aid, not authorization to mutate configuration.
