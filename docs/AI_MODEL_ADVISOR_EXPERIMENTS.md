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
- the workload profile and its cost/latency sensitivities at planning time;
- a shared `task_id` template;
- successful paired task IDs already present in feedback;
- paired tasks still needed before cost/latency efficiency becomes active;
- a machine-readable collection status;
- a machine-readable `next_action`;
- the rationale for the comparison.

The planner never calls a provider and never writes feedback. It is a controlled experiment manifest, not an autonomous benchmark executor.

### Collection lifecycle

The plan JSON exposes a stable lifecycle so automation does not need to parse Markdown:

- `planned` — no complete successful A/B task has been collected yet;
- `collecting` — at least one successful paired task exists but the paired-task threshold is not met;
- `ready` — the collection threshold has been met and the saved plan is ready for evaluation.

The plan also exposes `planned_experiments`, `collecting_experiments`, and `ready_experiments` totals.

`next_action` turns those states into an executable instruction for another agent or CI job:

- `collect_paired_tasks` with `paired_tasks_needed` while evidence is incomplete;
- `evaluate_saved_plan` when the collection threshold has been reached.

## Evaluate a fixed experiment plan

Keep the JSON created by `experiment-plan`. It freezes the exact A/B configurations and planning-time workload sensitivities so the comparison cannot silently change later when the router's ranking changes.

After recording or importing attempts whose `task_id` values follow the plan's template, evaluate the same plan:

```bash
python -m tools.ai_model_advisor.cli experiment-evaluate \
  --plan /tmp/model-experiments.json \
  --feedback ~/.hive/model-feedback.jsonl \
  --output /tmp/model-experiment-results.md \
  --json-output /tmp/model-experiment-results.json
```

`experiment-evaluate` is descriptive only. It never writes routing policy or modifies feedback. `experiment_eval.py` is the canonical implementation; the older `experiment_evaluate.py` module path is retained as a compatibility shim for scripts created while the feature branch was evolving.

For every planned pair the evaluator:

- matches only the exact A/B model + effort + execution configurations from the saved plan;
- matches only task IDs under that experiment's deterministic task-ID prefix;
- requires exactly one record per side for a task to become a complete pair;
- excludes duplicate attempts for either side as **ambiguous** instead of averaging or guessing which attempt should count;
- reports one-sided/incomplete task IDs separately;
- reports A/B success/partial/failure counts, mean retries, median USD cost, and median latency over the complete paired records;
- calculates a retry-aware quality score per paired task: `success=1`, `partial=0.5`, `failure=0`, reduced by the same capped retry penalty used by the feedback quality logic;
- reports A/B quality wins and ties plus the aggregate retry-aware quality delta;
- calculates a workload-weighted cost/latency efficiency delta only from paired tasks where **both sides succeeded**;
- uses the cost and latency sensitivities saved in the experiment plan rather than whatever the current workload happens to be later;
- exposes `decision`, `decision_basis`, `winner_side`, the winning configuration when one exists, and `policy_ready`;
- exposes a low/medium/high confidence band plus a numeric confidence score, explicitly labeled as a **heuristic evidence-strength indicator, not a probability or p-value**;
- exposes a machine-readable `next_action` for the current evaluation state;
- refuses to become policy-ready until the experiment has at least the required paired-task threshold.

### Evaluation lifecycle

Evaluation continues the same machine-readable state model:

- `planned` — zero complete paired tasks;
- `collecting` — some complete pairs exist but the threshold is not met;
- `ready` — the threshold is met but the evidence does not justify a directional winner;
- `decided` — the evaluator has a directional A/B winner from quality or aligned secondary evidence;
- `tradeoff` — the threshold is met but secondary evidence conflicts, so forcing a winner would hide a real cost/quality/retry trade-off.

The evaluation report includes `status_counts` for all five states. `ready` does **not** mean “adopt A”; it means the experiment has enough paired evidence to inspect, but no directional winner was justified by the evaluator.

The evaluator maps those states to explicit next actions:

- `planned` / `collecting` → `collect_paired_tasks` with an exact remaining count;
- `decided` → `review_winner_and_rerun_router` and the winning side;
- `tradeoff` → `review_tradeoff_or_collect_more`;
- `ready` with no directional winner → `review_tie_or_collect_more`.

These actions are advisory only. The evaluator never modifies routing policy automatically.

### Decision hierarchy

Quality comes first. If the retry-aware quality delta is materially positive or negative, the evaluator reports a quality lead even when the other side is cheaper or faster.

When quality is effectively tied, the evaluator looks at secondary evidence:

- **retry leader** from the complete paired records;
- **workload-weighted efficiency leader** from successful same-task cost/latency pairs.

If the available secondary signals agree, the evaluator can report an efficiency lead. If they disagree, it reports `tradeoff` rather than forcing a winner. Cost and latency leaders are also surfaced separately for diagnosis even though the decision uses their workload-weighted combination.

This evaluation does not replace the live feedback scorer. It exists to make controlled experiments interpretable before you decide whether the accumulated evidence is trustworthy enough to influence normal routing.

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
3. Run `experiment-plan` on the activity source you actually want to optimize and keep its JSON output.
4. Pick a proposed pair and use its shared `task_id` template for one real repeatable task.
5. Run the same logical task under both proposed configurations.
6. Record/import both outcomes with the same category and task ID.
7. Repeat on at least three comparable tasks before using latency/cost differences.
8. Run `experiment-evaluate` against the saved plan JSON.
9. Follow `next_action` to collect more evidence, inspect a trade-off/tie, or review a directional winner and re-run routing.
10. Re-run `feedback-report`, `feedback-readiness`, `experiment-plan`, and the routing matrix.

Do not optimize for token count alone. Token/cache/Hive-credit telemetry is retained for diagnostics, but it currently has no routing-score effect. A configuration that spends more tokens but succeeds more reliably can still be the better choice.

## Cache telemetry invariant

Hive-normalized `cached_tokens` and `cache_creation_tokens` are parts of input usage, not extra tokens to add on top. `UsageRecord` rejects telemetry where either cache count—or their combined total—exceeds `input_tokens` when the input total is known. This keeps audit cache ratios physically consistent and prevents corrupted manual data from looking like >100% cache efficiency.
