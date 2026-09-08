# AI Model Advisor — Controlled Experiments

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
- cross-config fallback is deliberately discounted to **35%** of equivalent exact evidence;
- **3 successful comparable task IDs** before paired cost/latency efficiency can change routing.

These values are imported from the router policy constants rather than duplicated in the report.

## Generate the next comparisons

`feedback-readiness` answers **what evidence is missing**. `experiment-plan` answers **what to compare next** based on current workload routing.

```bash
python -m tools.ai_model_advisor.cli experiment-plan \
  --input tools/ai_model_advisor/sample_activity.json \
  --feedback ~/.hive/model-feedback.jsonl \
  --output /tmp/model-experiments.md \
  --json-output /tmp/model-experiments.json
```

The command accepts the same explicit activity sources as the rest of the Advisor (`--input`, `--chatgpt-export`, or `--github-user`) plus optional provider filters.

For each recognized task category it proposes up to three controlled comparison lines:

1. **Model comparison** — the current best configuration against the strongest current configuration from a different model.
2. **Effort comparison** — the same model and execution mode, changing only reasoning effort.
3. **Execution comparison** — the same model and reasoning effort, changing only execution/orchestration mode.

The two same-model experiments deliberately change one variable at a time. This avoids attributing a result to `high` reasoning when the challenger also changed from `single` to `subagents`, or attributing a gain to orchestration when reasoning effort changed at the same time.

Each pair contains:

- a deterministic experiment ID based on category and both configurations;
- the exact A and B model/effort/execution settings;
- a shared `task_id` template;
- successful paired task IDs already present in feedback;
- paired tasks still needed before cost/latency efficiency becomes active;
- the rationale for the comparison.

The planner never calls a provider and never writes feedback. It is a controlled experiment manifest, not an autonomous benchmark executor.

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
2. Run `feedback-readiness` to see which evidence buckets are incomplete.
3. Run `experiment-plan` on the activity source you actually want to optimize.
4. Pick a proposed pair and use its shared `task_id` template for one real repeatable task.
5. Run the same logical task under both proposed configurations.
6. Record/import both outcomes with the same category and task ID.
7. Repeat on at least three comparable tasks before using latency/cost differences.
8. Re-run `feedback-report`, `feedback-readiness`, `experiment-plan`, and the routing matrix.

Do not optimize for token count alone. Token/cache/Hive-credit telemetry is retained for diagnostics, but it currently has no routing-score effect. A configuration that spends more tokens but succeeds more reliably can still be the better choice.

## Cache telemetry invariant

Hive-normalized `cached_tokens` and `cache_creation_tokens` are parts of input usage, not extra tokens to add on top. `UsageRecord` rejects telemetry where either cache count—or their combined total—exceeds `input_tokens` when the input total is known. This keeps audit cache ratios physically consistent and prevents corrupted manual data from looking like >100% cache efficiency.
