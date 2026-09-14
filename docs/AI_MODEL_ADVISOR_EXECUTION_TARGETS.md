# AI Model Advisor: Experiment Execution Targets

Experiment plans are intentionally host-agnostic when they are created. A recommendation such as `single`, `subagents`, or `dynamic_workflow` describes the desired execution shape; it does not prove that a particular host can execute it.

Before a plan enters a host-specific benchmark workflow, bind a copy of the saved plan to an explicit execution target.

For Hive:

```bash
python -m tools.ai_model_advisor.experiment_target \
  --plan model-advisor-output/experiment-plan.json \
  --host hive \
  --output model-advisor-output/experiment-plan.hive.json
```

For the built-in direct OpenAI/Anthropic API adapter:

```bash
python -m tools.ai_model_advisor.experiment_target \
  --plan model-advisor-output/experiment-plan.json \
  --host provider_api \
  --output model-advisor-output/experiment-plan.provider-api.json
```

The binding adds an explicit contract. Hive uses:

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

Direct provider execution uses:

```json
{
  "execution_target": {
    "host": "provider_api",
    "adapter": "provider_api",
    "adapter_contract_version": 1,
    "preflight_module": "tools.ai_model_advisor.provider_experiment_preflight"
  }
}
```

The source plan is not mutated. Rebinding a plan that already targets something different is rejected unless `--replace` is supplied deliberately.

## Why the target is separate from `execution_mode`

These are different facts:

- `execution_mode=single` describes how the task should be executed;
- `host=hive` or `host=provider_api` says where the task is intended to run;
- `adapter` identifies the evidence-producing integration;
- `adapter_contract_version=1` freezes the adapter semantics expected by the saved plan.

Keeping them separate prevents accidental equivalences such as “single means Hive” or “subagents means ChatGPT Work.”

## Apply-time provenance enforcement

Target binding is enforced during `experiment-run --apply`, not merely documented.

- An unbound legacy/generic plan may still use a custom runner for backward compatibility.
- A plan bound to `hive / hive_litellm` must use the canonical Hive adapter module.
- A plan bound to `provider_api / provider_api` must use the built-in direct provider adapter module.
- A mismatched runner is rejected **before** `command_executor` is created and before any provider credentials, network calls, or benchmark feedback are used.

To switch execution systems, rebind the saved plan deliberately with `experiment-target --replace`. Do not bypass provenance by swapping `--runner` argv on an already-bound plan.

## Hive preflight behavior

`tools.ai_model_advisor.hive_experiment_preflight` accepts legacy unbound plans for backward compatibility and labels them `unbound (legacy/generic plan)`.

When `execution_target` is present, Hive preflight fails closed if:

- `host` is not `hive`;
- `adapter` is not `hive_litellm`;
- `adapter_contract_version` is not supported;
- either A/B side requests a provider, execution mode, or runtime capability outside the current adapter contract.

A successful target check still does not prove that an individual model ID is supported by the installed LiteLLM/provider combination. Exact model and effort remain subject to post-transform wire verification during explicit `--apply` execution.

## Direct provider preflight behavior

Run the direct provider preflight before spending API credits:

```bash
OPENAI_API_KEY="..." ANTHROPIC_API_KEY="..." \
python -m tools.ai_model_advisor.provider_experiment_preflight \
  --plan model-advisor-output/experiment-plan.provider-api.json \
  --experiment-id implementation-model-abc123 \
  --require-ready
```

This preflight performs **no provider/network call**. It checks:

- the plan is explicitly bound to `provider_api / provider_api / contract-v1`;
- both sides use `execution_mode=single`;
- each provider/model pair exists in the committed Advisor registry;
- each requested effort is declared by that registry model, including `default` for models such as Claude Haiku 4.5 where Advisor intentionally omits an explicit effort control;
- the required `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` exists in the current environment.

Credential presence and registry compatibility are readiness checks, not proof of provider acceptance. The real adapter still has to execute the exact saved configuration, echo `applied_configuration`, and use deterministic acceptance criteria before feedback becomes model evidence.

## Recommended controlled flow

```text
experiment-plan.json
    -> bind execution target
    -> target-specific preflight
    -> generic preview
    -> explicit --apply with the matching bound adapter
    -> wire/applied-configuration proof
    -> deterministic judge
    -> paired feedback evidence
```

This keeps host provenance auditable without coupling the generic experiment planner to one execution environment, and makes a target/runner mismatch a hard error instead of a documentation-only warning.
