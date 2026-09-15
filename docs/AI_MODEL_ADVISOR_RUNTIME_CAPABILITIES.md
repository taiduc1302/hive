# AI Model Advisor: Hive Runtime Capability Probe

The runtime capability probe answers a narrower question than the model registry:

> What can this specific Hive installation prove it can execute **before** we spend money on a controlled benchmark?

Run it with:

```bash
python -m tools.ai_model_advisor.runtime_capabilities
python -m tools.ai_model_advisor.runtime_capabilities --json
```

The probe performs **no provider/network call** and requires no API credentials.

## What it checks

- the repository's exact LiteLLM pin, when present;
- the LiteLLM version installed in the Python environment;
- whether Hive's `LiteLLMProvider` transport imports successfully;
- whether Hive exposes the post-transform request capture used by the Advisor's fail-closed wire verification;
- whether the built-in Advisor adapter is ready to collect evidence for `execution_mode=single`.

The current Hive repository pins `litellm==1.83.4` in `core/pyproject.toml`. The probe reports a warning if the installed runtime differs from the repository pin, because benchmark evidence must describe the runtime that actually executed the request.

## Capability states

The probe deliberately distinguishes host capability from Advisor adapter coverage.

- `single`: supported by the current Hive LiteLLM Advisor adapter.
- `hive_agent_loop`, `dynamic_workflow`, `subagents`: Hive has related host machinery, but the Advisor does not yet claim those execution modes because there is no dedicated evidence-producing adapter for them.
- `chatgpt_work`: external ChatGPT host capability, not a native Hive control.
- `ultracode`: an orchestration label used by the Advisor for other execution environments, not a native Hive runtime knob.

This prevents the router from turning a recommendation label into a fabricated runtime control.

## Preflight one saved A/B experiment

Use the host-specific preflight before choosing the Hive adapter for a saved experiment:

```bash
python -m tools.ai_model_advisor.hive_experiment_preflight \
  --plan model-advisor-output/experiment-plan.json \
  --experiment-id <experiment-id>
```

Add `--json` for machine-readable output. Add `--require-ready` when a script should exit with status `2` if the pair cannot be executed by the current Hive Advisor adapter.

The preflight checks **both sides** of the saved pair. It blocks the pair when, for example:

- either side requests an execution mode other than `single`;
- either side uses a provider outside the current Hive Advisor adapter's `openai` / `anthropic` scope;
- model or effort identity is missing;
- Hive's post-transform wire-evidence transport is not available in the current runtime.

A `ready` result means the host plumbing and adapter coverage are present. It still does not claim that the pinned LiteLLM build supports a specific newly released model ID. The real `--apply` run remains the final proof boundary.

## Model and effort proof

A successful offline capability probe does **not** prove that a particular current model ID or effort level is supported by the pinned LiteLLM version.

For model-specific evidence, the real Hive adapter must still make the provider request and inspect Hive's post-transform captured request body. The adapter accepts evidence only when it can prove the requested model and effort semantics reached the wire. If the library/provider silently drops, rewrites, or cannot support the requested configuration, the run fails as infrastructure/configuration failure and writes no model-quality evidence.

`effort=default` keeps its existing meaning: send no explicit reasoning-effort control and verify that none appears in the post-transform body.

## Why this exists

The Advisor registry can be newer than the execution stack. A fresh registry entry means “this model currently exists according to the authoritative provider source”; it does **not** mean an older local LiteLLM build can route it correctly.

The capability/preflight path is:

```text
registry recommendation
    -> offline host capability probe
    -> saved-pair Hive preflight
    -> preview experiment
    -> explicit --apply
    -> post-transform wire proof
    -> deterministic judge
    -> paired feedback evidence
```

This keeps recommendation freshness, host compatibility, adapter coverage, and empirical evidence as separate facts instead of assuming one implies the others.
