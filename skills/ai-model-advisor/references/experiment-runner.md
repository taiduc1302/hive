# Controlled experiment runner

Load this reference when executing, previewing, or interpreting an Advisor controlled A/B experiment.

## Required lifecycle

Use this order:

1. Generate and save a fixed `experiment-plan` JSON.
2. Preview the selected pair with `experiment-run`; do not use `--apply` yet.
3. Confirm the adapter can honor the exact provider, model, effort, and execution mode shown for both sides.
4. Run with explicit `--apply --runner ...` only when real execution is intended.
5. Repeat on distinct benchmark tasks until the live evidence threshold is met.
6. Run `experiment-evaluate` on the same saved plan.
7. Run `experiment-impact` before changing normal routing defaults.

## Preview rule

Preview must be side-effect free:

- launch no adapter/provider;
- write no feedback;
- expose exact A/B configuration, deterministic task ID, order, live complete-pair count, source IDs, and benchmark SHA-256;
- do not place the benchmark prompt text into preview Markdown/JSON artifacts.

## Adapter protocol

Treat the external runner as an execution adapter, not as evidence by itself.

The adapter receives one schema-v1 JSON object on stdin containing:

- experiment ID and side;
- category/kind/task ID;
- benchmark task text and SHA-256;
- exact requested provider/model/effort/execution configuration.

The adapter's last non-empty stdout line must be a JSON object with:

- `schema_version: 1`;
- mandatory `applied_configuration` echoing the exact configuration actually used;
- `outcome`: `success`, `partial`, or `failure`;
- optional retries, latency, USD cost, token/cache telemetry, Hive credits, and note.

Reject evidence if `applied_configuration` differs from the saved plan. Never assume an environment honored `medium`, `xhigh`, `ultracode`, subagents, ChatGPT Work, or another control just because the plan requested it.

Use argv execution, not a shell string. Place `--runner` last because it consumes the remainder of the command line.

## Infrastructure vs model outcome

Do not record these as model failures:

- adapter could not start;
- timeout before trustworthy result;
- non-zero adapter exit;
- malformed/non-object JSON;
- schema mismatch;
- unsupported requested configuration;
- applied-configuration mismatch;
- missing telemetry needed by the adapter's own acceptance contract.

Those are infrastructure/configuration failures and should create no A/B evidence.

A model/task `failure` is valid only when the requested configuration actually ran and the benchmark acceptance criteria failed.

## Pair integrity

Use the same logical benchmark task for A and B. Let the runner alternate A→B and B→A between sequential task IDs unless a controlled protocol requires a fixed order.

Stage both validated sides before appending either. Recheck source IDs just before append. Treat duplicate/one-sided historical attempts according to the experiment evaluator's ambiguity rules rather than averaging them away.

The saved plan can become stale. Always use live feedback readiness before spending provider credits. Stop ordinary collection once the required complete-pair threshold is reached unless the user explicitly wants additional evidence.

## Example

Preview:

```bash
python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --output /tmp/run-preview.md \
  --json-output /tmp/run-preview.json
```

Apply only with a trusted adapter:

```bash
python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --apply \
  --output /tmp/run.md \
  --json-output /tmp/run.json \
  --runner python ./trusted_adapter.py
```
