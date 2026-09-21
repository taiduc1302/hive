# AI Model Advisor Hive Promotion Runtime Gate

AI Model Advisor v0.24 adds a fail-closed runtime gate between a reviewed Hive promotion preview and the operator's manual configuration edit.

The lifecycle is now:

1. collect controlled evidence;
2. evaluate a fresh canary;
3. build the manual promotion review;
4. generate the non-mutating Hive promotion preview;
5. prove that the current Hive runtime can carry the requested control;
6. prove the exact reviewed model + effort on Hive's post-transform LiteLLM wire path;
7. only then mark the promotion runtime-ready for a human edit;
8. after the edit, generate the existing verification receipt.

The gate never edits Hive configuration.

## Why this exists

A registry entry or a successful direct-provider experiment does not prove that the current Hive runtime can execute the same model and reasoning effort.

Hive pins LiteLLM independently from the Advisor registry. A model may exist in the registry while the installed Hive transport cannot yet route it correctly.

The gate therefore requires two separate facts:

- **host plumbing proof** from `runtime_capabilities`;
- **exact wire proof** from `hive_litellm_adapter`.

## Ready state

The gate returns:

- `runtime_ready_for_manual_hive_edit` only when the current Hive transport is ready and exact evidence matches the reviewed target;
- `blocked_runtime_plumbing` when the current Hive host cannot prove the necessary transport/config controls;
- `blocked_unproved_runtime` when no exact Hive wire evidence is supplied;
- `blocked_evidence_mismatch` when the evidence proves a different model/effort/configuration;
- `blocked_stale_runtime_evidence` when the evidence came from a different installed LiteLLM version;
- `blocked_invalid_preview` when the promotion preview violates the fail-closed contract.

## Exact evidence contract

The evidence must be a JSON result produced by the Hive LiteLLM adapter after its own wire verification.

Required fields include:

- `schema_version: 1`;
- `transport: "hive_litellm"`;
- exact `applied_configuration`;
- `litellm_version` matching the current runtime.

The gate uses the adapter result only as transport/configuration proof. Benchmark outcome remains a separate concern.

## Reasoning effort

For an explicit effort such as `high`, the current Hive capability report must prove native `reasoning_effort` passthrough.

For `effort=default`, no explicit effort key is required, so native effort passthrough is not a blocker.

## Privacy

The gate stores hashes of:

- the promotion preview;
- the runtime capability report;
- the Hive evidence artifact.

It does not copy provider response text or secrets into the gate result.

The reviewed target contains only:

- provider;
- model ID;
- effort;
- execution mode.

## Command

Generate current runtime capabilities:

    python -m tools.ai_model_advisor.runtime_capabilities --json \
      > model-advisor-output/hive-runtime-capabilities.json

Run the exact candidate through the Hive adapter using the existing controlled experiment runner and save its JSON adapter result.

Then evaluate the gate:

    python -m tools.ai_model_advisor.hive_promotion_gate \
      --promotion-preview model-advisor-output/hive-promotion-preview.json \
      --runtime-capabilities model-advisor-output/hive-runtime-capabilities.json \
      --hive-evidence model-advisor-output/hive-candidate-evidence.json \
      --json \
      --require-ready \
      --output model-advisor-output/hive-promotion-gate.json

Without `--hive-evidence`, the command still returns a report, but the gate remains fail-closed as `blocked_unproved_runtime`.

## Safety boundary

Even a ready gate reports:

- `safe_to_auto_apply: false`;
- `automatic_config_mutation: false`;
- `requires_human_approval: true`.

Runtime proof is evidence that a reviewed transition can be represented by the current Hive transport. It is not permission for automatic routing mutation, automatic rollback, credential changes, or orchestration changes.
