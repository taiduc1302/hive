# AI Model Advisor Hive Promotion Verification Receipt

AI Model Advisor v0.23 adds the post-application verification stage for reviewed Hive promotions.

The lifecycle is now:

1. collect controlled evidence;
2. evaluate a fresh canary;
3. build the manual promotion review;
4. generate the non-mutating Hive promotion preview;
5. a human operator decides whether to edit Hive configuration;
6. verify the resulting Hive configuration against the exact reviewed transition;
7. store the verification receipt as an audit artifact.

The receipt never changes Hive configuration.

## States

The verifier returns one of four states:

- `applied_exactly`: every reviewed Hive section exactly matches the approved after configuration;
- `not_applied`: every reviewed Hive section still matches the reviewed before configuration;
- `drifted`: the config is mixed, partially applied, or otherwise differs from both complete reviewed states;
- `blocked_invalid_preview`: the supplied preview violates the fail-closed review contract.

For `scope=both`, queen and worker sections must move together. One section on the new route and one section on the old route is `drifted`, not success.

## Default effort

A reviewed target with `effort=default` expects the explicit `reasoning_effort` key to be absent.

This matches the promotion preview's RFC 7396 merge-patch semantics: `reasoning_effort: null` removes the explicit override and restores provider-default behavior. A literal JSON null left in the file is reported as drift rather than exact application.

## Privacy

The verifier hashes the complete preview and current config for audit linkage, but the receipt never copies the full Hive configuration.

Only these current values are emitted for reviewed sections:

- provider;
- model;
- reasoning effort;
- whether the reasoning-effort key is present.

API keys, credentials, API bases, tokens, and unrelated config sections are not copied into Markdown or JSON receipts.

## Command

    python -m tools.ai_model_advisor.cli hive-promotion-receipt \
      --promotion-preview model-advisor-output/hive-promotion-preview.json \
      --hive-config /path/to/configuration.json \
      --output model-advisor-output/hive-promotion-receipt.md \
      --json-output model-advisor-output/hive-promotion-receipt.json

The standalone entry point is also available:

    python -m tools.ai_model_advisor.hive_promotion_receipt \
      --promotion-preview model-advisor-output/hive-promotion-preview.json \
      --hive-config /path/to/configuration.json \
      --json

## Audit hashes

A successful or drifted receipt includes:

- `preview_sha256`: canonical hash of the reviewed promotion preview;
- `current_config_sha256`: canonical hash of the full current Hive config;
- `receipt_sha256`: canonical hash of the receipt's review identity and section observations.

These hashes make it possible to prove which preview and config snapshot were verified without storing secrets in the receipt.

## Safety boundary

The verifier is read-only.

It reports:

- `safe_to_auto_mutate: false`;
- `automatic_config_mutation: false`;
- `requires_human_review: true`.

A receipt is evidence about configuration state, not permission for automatic routing mutation, automatic rollback, or provider execution.
