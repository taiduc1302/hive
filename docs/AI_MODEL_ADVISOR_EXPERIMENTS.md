# AI Model Advisor — Experiment Readiness

The Advisor can learn from ordinary Hive history, but historical observations do not prove which exact reasoning effort or orchestration mode caused a result. Use controlled repeated tasks when you want the router to learn configuration-level preferences instead of only model-level tendencies.

## Check what evidence is missing

```bash
python -m tools.ai_model_advisor.cli feedback-readiness \
  --feedback ~/.hive/model-feedback.jsonl \
  --output /tmp/model-readiness.md \
  --json-output /tmp/model-readiness.json
```

The report uses the same thresholds as the live router:

- **3 exact observations** before one exact model + effort + execution configuration can independently change quality scoring;
- **6 category-compatible observations** before same-model cross-config fallback can transfer quality evidence;
- **3 successful comparable task IDs** before paired cost/latency efficiency can change routing.

These values are imported from the router policy constants rather than duplicated in the report.

## Historical vs controlled evidence

Bulk Hive imports normally use:

- `effort=observed`
- `execution=hive_agent_loop`

Those records are useful model-level history. They can eventually activate the conservative same-model fallback, but they are not treated as controlled evidence for a particular effort or execution mode.

A controlled record should have an explicit configuration, for example:

```text
model: gpt-5.6-terra
effort: medium
execution: single
task_category: implementation
task_id: endpoint-benchmark-04
```

Run the same logical task under another configuration with the **same `task_id`**. This gives the Advisor a defensible paired comparison instead of comparing unrelated tasks by absolute seconds or dollars.

## What “fully evidence-ready” means

`feedback-readiness` marks a configuration fully ready only when all of these are true:

1. effort and execution mode are explicit rather than historical placeholders;
2. the exact configuration has reached the 3-observation quality threshold;
3. at least 3 successful shared task IDs exist against one or more different configurations.

The report still shows partial progress. For each observed configuration it calculates:

- exact outcome runs still needed;
- remaining observations before same-model fallback is available;
- paired task IDs still needed for efficiency scoring;
- whether quality evidence is exact, same-model fallback, or below threshold;
- the next evidence-collection action.

## Suggested experiment loop

1. Import existing Hive history and run `feedback-report`.
2. Run `feedback-readiness` and choose a category with incomplete evidence.
3. Pick one repeatable real task or benchmark and assign a stable `task_id`.
4. Run it with two configurations you genuinely might choose between.
5. Record/import both outcomes with the same category and task ID.
6. Repeat on at least three comparable tasks before using latency/cost differences.
7. Re-run `feedback-report`, `feedback-readiness`, and the routing matrix.

Do not optimize for token count alone. Token/cache/Hive-credit telemetry is retained for diagnostics, but it currently has no routing-score effect. A configuration that spends more tokens but succeeds more reliably can still be the better choice.

## Cache telemetry invariant

Hive-normalized `cached_tokens` and `cache_creation_tokens` are parts of input usage, not extra tokens to add on top. `UsageRecord` rejects telemetry where either cache count—or their combined total—exceeds `input_tokens` when the input total is known. This keeps audit cache ratios physically consistent and prevents corrupted manual data from looking like >100% cache efficiency.
