# AI Model Advisor: Experiment Execution Targets

Experiment plans are intentionally host-agnostic when they are created. A recommendation such as `single`, `subagents`, or `dynamic_workflow` describes the desired execution shape; it does not prove that a particular host can execute it.

Before a plan enters a host-specific benchmark workflow, bind a copy of the saved plan to an explicit execution target:

```bash
python -m tools.ai_model_advisor.experiment_target \
  --plan model-advisor-output/experiment-plan.json \
  --host hive \
  --output model-advisor-output/experiment-plan.hive.json
```

The binding adds:

```json
{
  "execution_target": {
    "host": "hive",
    "adapter": "hive_litellm",
    "adapter_contract_version": 1,
    "preflight_module": "tools.ai_model_advisor.hive_experiment_preflight"
  }
}
```

The source plan is not mutated. Rebinding a plan that already targets something different is rejected unless `--replace` is supplied deliberately.

## Why the target is separate from `execution_mode`

These are different facts:

- `execution_mode=single` describes how the task should be executed;
- `host=hive` says where the task is intended to run;
- `adapter=hive_litellm` identifies the evidence-producing integration;
- `adapter_contract_version=1` freezes the adapter semantics expected by the saved plan.

Keeping them separate prevents accidental equivalences such as “single means Hive” or “subagents means ChatGPT Work.”

## Hive preflight behavior

`tools.ai_model_advisor.hive_experiment_preflight` accepts legacy unbound plans for backward compatibility and labels them `unbound (legacy/generic plan)`.

When `execution_target` is present, Hive preflight fails closed if:

- `host` is not `hive`;
- `adapter` is not `hive_litellm`;
- `adapter_contract_version` is not supported;
- either A/B side requests a provider, execution mode, or runtime capability outside the current adapter contract.

A successful target check still does not prove that an individual model ID is supported by the installed LiteLLM/provider combination. Exact model and effort remain subject to post-transform wire verification during explicit `--apply` execution.

## Recommended controlled flow

```text
experiment-plan.json
    -> bind execution target
    -> host-specific preflight
    -> generic preview
    -> explicit --apply with the matching adapter
    -> wire proof
    -> deterministic judge
    -> paired feedback evidence
```

This makes host provenance auditable without coupling the generic experiment planner to Hive or any future execution environment.
