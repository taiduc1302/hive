# AI Model Advisor: Hive LiteLLM Adapter

The Hive adapter lets a controlled AI Model Advisor experiment use the same `LiteLLMProvider` transport stack as Hive without mutating the user's persistent Hive configuration.

## Safety boundary

The adapter intentionally supports only `execution_mode=single`. It does **not** pretend that one LiteLLM call is equivalent to Hive AgentLoop, colony execution, subagents, ChatGPT Work, ultracode, or any other orchestration mode.

Every run also requires `acceptance_mode=external_judge`. A provider returning HTTP 200 is transport success, not benchmark success.

## Isolation

The adapter creates a temporary `HIVE_HOME` before importing the Hive framework. This keeps failed-request dumps and any runtime-local state out of the user's normal Hive home. The previous `HIVE_HOME` value is restored before the adapter exits.

Provider credentials are read from the environment only:

- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`

The adapter never includes API keys or captured request headers in its JSON result.

## Wire-level configuration proof

Hive already installs a LiteLLM pre-call capture hook that records the provider request **after LiteLLM transforms it**. The adapter uses that capture as an acceptance gate before returning evidence.

For OpenAI, a non-default effort must be visible via either `reasoning_effort` or `reasoning.effort`.

For Anthropic, a non-default effort must be visible via `output_config.effort`.

`effort=default` has the same meaning as the direct provider adapter: do not send an explicit effort knob. The wire proof therefore requires those explicit effort fields to be absent rather than pretending that `default` is a provider effort level.

The outgoing model must also match the requested model. If the pinned LiteLLM version silently drops, rewrites, invents, or cannot support the requested setting, the adapter exits non-zero and the experiment runner must not append model evidence.

This is important because Hive currently pins `litellm==1.83.4` while the Advisor registry can know about newer models. Registry freshness does not imply runtime compatibility.

## Invocation

Use it anywhere `experiment-run` accepts an adapter command:

```text
python -m tools.ai_model_advisor.hive_litellm_adapter
```

The adapter consumes the standard runner JSON payload on stdin and emits the standard runner result JSON on stdout.

A minimal payload has this shape:

```json
{
  "schema_version": 1,
  "acceptance_mode": "external_judge",
  "task": "Return exactly 42.",
  "configuration": {
    "provider": "openai",
    "model_id": "gpt-6-astra",
    "effort": "high",
    "execution_mode": "single"
  }
}
```

Successful transport returns `outcome: "partial"`; the deterministic judge is still responsible for converting the candidate output into benchmark success/failure.

## Runtime compatibility is deliberately fail-closed

Do not add a model/version allowlist just because the Advisor registry lists a model. Let the installed Hive/LiteLLM stack attempt the request, then require the captured post-transform body to prove that the requested model and effort semantics were actually applied.

A future AgentLoop/colony adapter should use the same principle: prove host-specific execution controls from runtime telemetry instead of inferring them from configuration intent.
