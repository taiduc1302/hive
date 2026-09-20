# AI Model Advisor Hive Promotion Preview

AI Model Advisor v0.22 connects the fail-closed promotion-review package to a concrete, still non-mutating Hive configuration preview.

The flow becomes:

1. collect controlled evidence;
2. evaluate the fresh canary;
3. build a promotion review and verify route drift;
4. select a `ready_for_manual_edit` category;
5. translate that reviewed transition into exact Hive apply and rollback merge-patch previews;
6. a human operator verifies the current config and decides whether to edit it.

## Safety boundary

The preview never writes `configuration.json`.

It reports:

- `safe_to_auto_apply: false`;
- `automatic_config_mutation: false`;
- the reviewed `change_id`;
- exact apply patch;
- exact rollback patch;
- expected current provider/model/effort/execution configuration;
- Hive sections that must be verified before any manual edit.

## Fail-closed limits

A preview is produced only when:

- the promotion review state is `ready_for_manual_edit`;
- the review still requires human approval;
- the reviewed rollback configuration exactly equals the reviewed before configuration;
- provider is unchanged;
- before, after, and rollback all use `execution_mode=single`.

Cross-provider transitions are blocked because credentials and API-base requirements are outside the review package.

Non-single execution transitions are blocked because Hive model/reasoning config does not prove orchestration controls such as subagents or dynamic workflows.

## Command

    python -m tools.ai_model_advisor.cli hive-promotion-preview \
      --promotion-review model-advisor-output/promotion-review.json \
      --category debugging \
      --scope queen \
      --output model-advisor-output/hive-promotion-preview.md \
      --json-output model-advisor-output/hive-promotion-preview.json

The standalone `python -m tools.ai_model_advisor.hive_promotion_preview` entry point remains available and supports `--json`.

For `effort=default`, the patch emits `reasoning_effort: null`; under the documented merge-patch semantics, that removes an explicit override and restores provider default behavior.

The generated patch may change the model inside the already-reviewed provider, but never changes provider credentials, API base, or orchestration.
