# AI Model Advisor controlled experiment runner

The experiment runner closes the gap between a saved `experiment-plan` and empirical feedback. It does **not** assume that Hive, OpenAI, Anthropic, Claude Code, Codex, or another host exposes the same controls. Instead, an execution adapter translates the saved Advisor configuration into a real run, and an optional deterministic judge can independently decide whether the benchmark passed.

The design goal is evidence integrity: never label a run as `model / effort / execution` unless the execution system actually honored those settings, and never confuse HTTP/process completion with benchmark success.

## Safety model

- Preview is the default. Without `--apply`, no executable is launched and no feedback is written.
- Preview artifacts retain the benchmark SHA-256 but intentionally omit benchmark prompt text.
- `--runner` and `--judge` are argv, not shell strings. Execution uses `subprocess.run(..., shell=False)` semantics.
- The same benchmark task text is sent to A and B. The run report records its SHA-256 fingerprint.
- Automatic order alternates A→B, then B→A across sequential task IDs to reduce warm-cache/order bias.
- Both sides must return valid structured results before either side is appended to feedback.
- Adapter/judge timeout, launch failure, non-zero exit, malformed JSON, schema mismatch, or applied-configuration mismatch is infrastructure failure. It is **not** model failure and creates no paired evidence.
- The runner recomputes completed A/B pairs from live feedback. A stale saved plan cannot silently cause another run after the quality threshold is already reached unless `--allow-ready` is explicit.
- Source IDs are checked before execution and again before append. Callers should still serialize concurrent writers because the JSONL store is not a distributed lock.

## Preview

Use either the main Advisor CLI or the standalone execution module:

```bash
python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --output /tmp/experiment-run-preview.md \
  --json-output /tmp/experiment-run-preview.json
```

Preview reports:

- exact A and B provider/model/effort/execution configuration;
- current live complete-pair count;
- next deterministic experiment task ID;
- A/B execution order;
- benchmark SHA-256;
- source IDs that would be written.

It does not require an adapter or API key.

## Apply with an adapter

Only add `--apply` after reviewing preview and confirming that the adapter can actually honor **all** four configuration fields.

`--runner` consumes the rest of the command line, so put it last:

```bash
python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --apply \
  --timeout-seconds 1800 \
  --output /tmp/experiment-run.md \
  --json-output /tmp/experiment-run.json \
  --runner python ./my_model_adapter.py
```

Each side launches the same adapter executable as a separate process. One JSON request is written to stdin. The adapter may write diagnostics, but its **last non-empty stdout line** must be the JSON result object. Prefer diagnostics on stderr so machine output stays simple.

## Request schema v1

Example request:

```json
{
  "schema_version": 1,
  "experiment_id": "implementation-model-abc123",
  "side": "A",
  "category": "implementation",
  "kind": "model",
  "task_id": "implementation-abc123-task-01",
  "task": "Implement the fixed benchmark endpoint...",
  "task_sha256": "...",
  "configuration": {
    "provider": "anthropic",
    "model_id": "claude-sonnet-5",
    "effort": "medium",
    "execution_mode": "single"
  }
}
```

The adapter owns translation from this neutral configuration to the real execution environment. If an environment cannot set a requested field, the adapter must refuse the run rather than silently downgrade it.

When a deterministic `--judge` is present, the runner also sets:

```json
{"acceptance_mode": "external_judge"}
```

This lets an adapter distinguish transport/execution from benchmark acceptance.

## Adapter result schema v1

Minimum result without a separate judge:

```json
{
  "schema_version": 1,
  "applied_configuration": {
    "provider": "anthropic",
    "model_id": "claude-sonnet-5",
    "effort": "medium",
    "execution_mode": "single"
  },
  "outcome": "success"
}
```

`applied_configuration` is mandatory and is compared with the saved experiment configuration before evidence is accepted.

Optional fields can include candidate output/artifact metadata plus:

```json
{
  "retries": 0,
  "latency_seconds": 42.4,
  "cost_usd": 0.31,
  "input_tokens": 12400,
  "output_tokens": 2100,
  "cached_tokens": 8000,
  "cache_creation_tokens": 0,
  "credits": 1.7,
  "note": "transport completed"
}
```

If `latency_seconds` is omitted, the wrapper's wall-clock adapter runtime is used. An adapter should provide provider/execution latency itself when process startup or local setup time would materially distort the comparison.

## Deterministic outcome judge

Prefer a deterministic checker whenever benchmark correctness can be objectively evaluated: tests, schema validation, expected values, static analysis, artifact hashes, or other fixed acceptance criteria.

Place `--judge` before `--runner` because `--runner` consumes the remaining argv:

```bash
python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --apply \
  --judge python ./check_endpoint.py \
  --output /tmp/experiment-run.md \
  --json-output /tmp/experiment-run.json \
  --runner python ./my_model_adapter.py
```

The judge receives task/configuration metadata plus the complete adapter result and returns:

```json
{
  "schema_version": 1,
  "outcome": "success",
  "note": "acceptance checks passed"
}
```

The judge's `outcome` replaces the adapter self-report. Adapter latency/cost/token telemetry is preserved; judge overhead is not treated as model latency. Judge infrastructure failure invalidates the pair rather than creating model-failure evidence.

## Built-in direct OpenAI / Anthropic adapter

For **single-model API** benchmarks, the repository includes:

```bash
python -m tools.ai_model_advisor.provider_api_adapter
```

It intentionally has a narrow scope:

- only `execution_mode=single`;
- OpenAI uses the Responses API and maps Advisor effort to `reasoning.effort`;
- Anthropic uses the Messages API and maps Advisor effort to `output_config.effort`;
- API keys come only from `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`;
- `AI_MODEL_ADVISOR_MAX_OUTPUT_TOKENS` can set the common output cap (default 32768);
- `AI_MODEL_ADVISOR_PROVIDER_TIMEOUT_SECONDS` can set the inner HTTP timeout (default 300);
- raw API keys are never included in adapter output;
- response text plus token/cache telemetry is returned to the judge;
- Anthropic input usage is normalized as uncached + cache-read + cache-creation tokens so Advisor cache invariants remain valid;
- when OpenAI explicitly echoes `reasoning.effort`, a mismatch with the saved experiment is rejected.

The direct provider adapter **requires** `acceptance_mode=external_judge`. In practical terms, use it only with `--judge`. A successful HTTP response is transport success, not benchmark success.

Example:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."

python -m tools.ai_model_advisor.cli experiment-run \
  --plan /tmp/experiment-plan.json \
  --experiment-id implementation-model-abc123 \
  --feedback ~/.hive/model-feedback.jsonl \
  --task-file ./benchmarks/endpoint-01.md \
  --apply \
  --judge python ./check_endpoint.py \
  --runner python -m tools.ai_model_advisor.provider_api_adapter
```

If a planned comparison uses `chatgpt_work`, `ultracode`, `subagents`, `dynamic_workflow`, or another orchestration mode, use a host-specific adapter instead. The direct API adapter rejects those modes rather than pretending a single API call is equivalent.

The direct adapter currently records provider token/cache metrics and wrapper latency. It does not invent `cost_usd` when the provider response does not contain an authoritative request cost; paired cost scoring can remain absent while latency/token diagnostics are still retained.

## Outcome semantics

`outcome` must be one of:

- `success`: benchmark acceptance criteria passed;
- `partial`: usable but incomplete/degraded result;
- `failure`: the requested configuration ran correctly, but the benchmark failed its acceptance criteria.

Do not encode API outages, authentication failures, unsupported configuration fields, adapter exceptions, malformed telemetry, or checker failures as `failure`. Those are infrastructure failures and should exit non-zero or return a result that fails protocol validation.

## Adapter responsibilities

A production adapter should:

1. parse exactly one request JSON object from stdin;
2. validate that it can honor the requested provider, model, effort, and execution mode;
3. execute the fixed benchmark without changing acceptance criteria between A and B;
4. expose candidate output/artifacts to a deterministic judge when possible;
5. collect real latency/cost/token telemetry from the execution system;
6. return schema v1 and the exact `applied_configuration`;
7. exit non-zero on infrastructure/configuration errors.

The generic runner deliberately does not infer that provider-specific controls are equivalent. `ultracode`, a multi-agent workflow, a ChatGPT Work handoff, and an API reasoning-effort parameter are different execution mechanisms even if all can increase capability.

## Evidence lifecycle

A normal controlled experiment is now:

```text
experiment-plan
    ↓
experiment-run preview
    ↓ explicit --apply + trusted adapter + deterministic judge when feasible
paired feedback JSONL
    ↓ repeat on distinct benchmark tasks
experiment-evaluate
    ↓
experiment-impact
```

Quality evidence can become ready after three unambiguous complete A/B task IDs. Paired cost/latency efficiency requires successful comparable tasks under the existing Advisor policy. The runner stops automatically once live quality evidence is ready unless extra collection is explicitly requested.
