# Controlled experiment runner

Load this reference when executing, previewing, or interpreting an Advisor controlled A/B experiment.

## Required lifecycle

Use this order:

1. Generate and save a fixed `experiment-plan` JSON.
2. Preview the selected pair with `experiment-run`; do not use `--apply` yet.
3. Confirm the adapter can honor the exact provider, model, effort, and execution mode shown for both sides.
4. When objective acceptance criteria can be automated, use a deterministic judge and prefer it over adapter self-reporting.
5. Run with explicit `--apply --runner ...` only when real execution is intended.
6. Repeat on distinct benchmark tasks until the live evidence threshold is met.
7. Run `experiment-evaluate` on the same saved plan.
8. Run `experiment-impact` before changing normal routing defaults.

## Preview rule

Preview must be side-effect free:

- launch no adapter/provider;
- write no feedback;
- expose exact A/B configuration, deterministic task ID, order, live complete-pair count, source IDs, and benchmark SHA-256;
- do not place benchmark prompt text into preview Markdown/JSON artifacts.

## Adapter protocol

Treat the runner as an execution adapter, not as evidence by itself. It receives schema-v1 JSON with experiment/task metadata plus exact provider/model/effort/execution configuration and returns schema v1 with mandatory `applied_configuration`.

Reject evidence when `applied_configuration` differs from the saved plan. Never assume an environment honored `medium`, `xhigh`, `ultracode`, subagents, ChatGPT Work, or another control merely because the plan requested it.

Use `effort=default` only when the registry intentionally means **provider default with no explicit effort knob sent**. This is not an alias for `medium` or `high`, and it must not be treated as proof that a provider used a specific internal reasoning depth.

Use argv execution, not a shell string. Place `--runner` last because it consumes the remainder of the command line.

## Built-in direct provider adapter

For single-call API benchmarks, use:

```bash
python -m tools.ai_model_advisor.provider_api_adapter
```

Rules:

- only `execution_mode=single` is supported;
- OpenAI maps non-default Advisor effort to Responses API `reasoning.effort`; `effort=default` omits the reasoning-effort field;
- Anthropic maps non-default Advisor effort to Messages API `output_config.effort`; `effort=default` omits `output_config` entirely;
- use `effort=default` for registry models whose current provider API exposes no supported effort control, such as Claude Haiku 4.5;
- keys come only from `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`;
- unsupported orchestration modes are rejected rather than approximated;
- the adapter requires `acceptance_mode=external_judge`, so use it only with a deterministic judge;
- the adapter returns candidate `response_text` plus token/cache telemetry for judging/diagnostics;
- Anthropic cache-read/cache-creation usage is normalized into total input tokens for Advisor invariants;
- if OpenAI explicitly echoes a different reasoning effort for a non-default request, reject the observation;
- do not invent USD cost when the API response does not contain authoritative request cost.

## Deterministic outcome judge

Prefer a judge when correctness can be checked independently: tests, schema validation, fixed expected values, artifact checks, static analysis, or another deterministic contract.

The judge receives task/configuration metadata plus the complete adapter result. It returns schema v1 with `outcome` = `success`, `partial`, or `failure`. Its outcome replaces adapter self-report while adapter latency/cost/token telemetry stays unchanged.

Judge timeout, launch failure, non-zero exit, malformed JSON, invalid schema/outcome, or checker-infrastructure failure invalidates the pair and creates no model evidence.

### Built-in expected-output judge

For simple text/JSON benchmarks, use `expected_output_judge` with modes `exact`, `strip-exact`, `contains`, or `json-equal`.

Standalone runner can use the in-process flags:

```bash
python -m tools.ai_model_advisor.experiment_run_cli \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/task.md \
  --apply \
  --expected-output-file ./benchmarks/expected.json \
  --expected-output-mode json-equal \
  --runner python -m tools.ai_model_advisor.provider_api_adapter
```

For the main Advisor CLI, configure the same judge through environment variables so the nested judge command needs no flags:

```bash
export AI_MODEL_ADVISOR_EXPECTED_OUTPUT_FILE=./benchmarks/expected.json
export AI_MODEL_ADVISOR_EXPECTED_OUTPUT_MODE=json-equal

python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/task.md \
  --apply \
  --judge python -m tools.ai_model_advisor.expected_output_judge \
  --runner python -m tools.ai_model_advisor.provider_api_adapter
```

Invalid expected JSON is checker configuration failure. Invalid candidate JSON is a benchmark/model failure under `json-equal`.

## Infrastructure vs model outcome

Do not record these as model failures:

- adapter or judge could not start;
- timeout before trustworthy result;
- non-zero adapter/judge exit;
- malformed/non-object JSON;
- schema mismatch;
- unsupported requested configuration;
- applied-configuration mismatch;
- acceptance-checker infrastructure failure.

A model/task `failure` is valid only when the requested configuration actually ran and the fixed acceptance criteria failed.

## Pair integrity

Use the same logical benchmark task for A and B. Let the runner alternate A→B and B→A between sequential task IDs unless a controlled protocol requires fixed order.

Stage both validated sides before appending either. Recheck source IDs just before append. Treat duplicate/one-sided historical attempts according to evaluator ambiguity rules rather than averaging them away.

The saved plan can become stale. Always use live feedback readiness before spending provider credits. Stop ordinary collection once the required complete-pair threshold is reached unless extra evidence is explicitly requested.
