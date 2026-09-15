# AI Model Advisor: Experiment Execution Targets

Experiment plans are intentionally host-agnostic when they are created. A recommendation such as `single`, `subagents`, or `dynamic_workflow` describes the desired execution shape; it does not prove that a particular host can execute it.

## Discover the supported target catalog

Execution-target capabilities are declared once in `tools.ai_model_advisor.execution_targets` and are available as Markdown or JSON:

```bash
python -m tools.ai_model_advisor.execution_targets
python -m tools.ai_model_advisor.execution_targets --json
```

The catalog is the shared source of truth for:

- host and adapter identity;
- adapter contract version;
- canonical runner module;
- preflight module;
- supported providers;
- supported execution modes;
- deterministic-judge requirement;
- provider credential environment variables;
- evidence method used by the target.

`experiment_target`, Hive preflight, direct-provider preflight, canonical runner resolution, and judge policy consume this catalog instead of maintaining separate target tables.

## Bind a plan to an execution target

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

The binding adds a compact immutable target identity. Hive uses:

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
- Prefer `--use-target` for bound plans; it resolves canonical runner argv from the catalog automatically.
- `--use-target` and manual `--runner` are mutually exclusive.
- Built-in bound targets require deterministic acceptance evidence before the adapter process launches.
- A mismatched runner is rejected before `command_executor` is created and before any provider credentials, network calls, or benchmark feedback are used.

To switch execution systems, rebind the saved plan deliberately with `experiment-target --replace`. Do not bypass provenance by swapping `--runner` argv on an already-bound plan.

## Hive preflight behavior

`tools.ai_model_advisor.hive_experiment_preflight` accepts legacy unbound plans for backward compatibility and labels them `unbound (legacy/generic plan)`.

Shared provider/mode/target checks come from the execution-target catalog. Hive-specific preflight additionally verifies the local runtime's single-call post-transform wire-evidence readiness.

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

This preflight performs **no provider/network call**. Shared target/provider/mode/credential rules come from the catalog. Direct-provider-specific checks additionally verify that each provider/model pair exists in the Advisor registry and that the requested effort is declared for that model, including `default` for models such as Claude Haiku 4.5 where Advisor intentionally omits an explicit effort control.

Credential presence and registry compatibility are readiness checks, not proof of provider acceptance. The real adapter still has to execute the exact saved configuration, echo `applied_configuration`, and use deterministic acceptance criteria before feedback becomes model evidence.

## Recommended controlled flow

```text
execution-target catalog
    -> experiment-plan.json
    -> bind execution target
    -> target-specific preflight
    -> generic preview
    -> explicit --apply --use-target + deterministic judge
    -> wire/applied-configuration proof
    -> paired feedback evidence
```

This keeps host provenance auditable without coupling the generic experiment planner to one execution environment, and makes target/runner/policy drift testable instead of documentation-only.
