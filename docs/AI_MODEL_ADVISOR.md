# AI Model Advisor

`tools/ai_model_advisor` is a model watcher and workload router for Hive.

It answers four questions separately:

1. **What models and execution modes are current?** Scan official provider documentation and flag model/mode signals that changed.
2. **What should I use for my work overall?** Convert recent activity into a workload profile and recommend a model + reasoning effort + execution mode.
3. **Which model should I use for each kind of work?** Build a routing matrix for implementation, debugging, architecture, repo review, research, automation, documents, and spreadsheet-heavy work that actually appears in the supplied activity.
4. **What actually works for me?** Record real outcomes and use repeated evidence to personalize later recommendations.

## Registry policy

The committed registry is a verified snapshot, not a permanent source of truth. Every model entry points to official provider sources. A scheduled scan detects drift.

- Official provider docs and release notes are authoritative.
- Community posts are discovery signals only until officially confirmed.
- Limited-rollout models are excluded from normal recommendations by default.
- Model, reasoning effort, and orchestration mode are stored separately.

## Stateful source monitoring

The daily GitHub Action persists a compact fingerprint baseline in GitHub Actions cache. Each new run compares current official model/mode signals with the previous successful baseline, so `changed_sources` means an actual observed change between runs rather than simply “the page exists today.”

If an official source temporarily fails to load, its previous hash is preserved. A transient network failure therefore does not erase history and create a false change on the following run.

## Privacy boundary

There is no hidden API in this project for reading a user's entire ChatGPT profile/history. The advisor analyzes only data explicitly available to it: a supplied ChatGPT export, generic activity JSON, GitHub events, explicitly authorized sources, or local Hive telemetry that the user deliberately points the importer at.

## CLI

```bash
python -m tools.ai_model_advisor.cli profile --input tools/ai_model_advisor/sample_activity.json --output /tmp/workload.json
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --output /tmp/recommendation.md
python -m tools.ai_model_advisor.cli matrix --input tools/ai_model_advisor/sample_activity.json --output /tmp/routing-matrix.md --json-output /tmp/routing-matrix.json
python -m tools.ai_model_advisor.cli matrix --chatgpt-export /path/to/conversations.json --feedback ~/.hive/model-feedback.jsonl --output /tmp/personal-routing.md
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --feedback ~/.hive/model-feedback.jsonl --output /tmp/personalized.md
python -m tools.ai_model_advisor.cli feedback-add --feedback ~/.hive/model-feedback.jsonl --provider anthropic --model claude-sonnet-5 --effort high --execution single --outcome success --retries 0 --latency-seconds 42 --cost-usd 0.31 --task-category implementation --task-id api-endpoint-17
python -m tools.ai_model_advisor.cli feedback-import-hive --events /path/to/session/events.jsonl --details /path/to/session/logs/details.jsonl --feedback ~/.hive/model-feedback.jsonl --task-category repo_review --output /tmp/hive-feedback-import.md
python -m tools.ai_model_advisor.cli feedback-import-hive-root --root ~/.hive --feedback ~/.hive/model-feedback.jsonl --output /tmp/hive-history-preview.md
python -m tools.ai_model_advisor.cli feedback-import-hive-root --root ~/.hive --feedback ~/.hive/model-feedback.jsonl --apply --output /tmp/hive-history-import.md
python -m tools.ai_model_advisor.cli scan --baseline /tmp/source-baseline.json --write-baseline /tmp/source-baseline.json --output /tmp/source-scan.md --json-output /tmp/source-scan.json
```

## Routing matrix

`matrix` groups the supplied activity by recognized task category and builds a separate workload profile for each category. One activity may contribute to multiple relevant categories (for example, a repository-wide debugging task can be both `debugging` and `repo_review`), but each routing row is forced to its own category before personal feedback is applied.

The Markdown output includes the primary model/effort/execution configuration plus alternatives and a switch-up trigger. The scheduled GitHub Action publishes the overall recommendation, task routing matrix, and source-change scan directly in the Actions Job Summary and also stores Markdown/JSON copies in the workflow artifact.

## Personal feedback policy

Feedback is JSONL and stays local unless the user deliberately commits/uploads it. A record can include outcome (`success`, `partial`, `failure`), retries, latency, cost, task category, a stable task ID, an importer source ID, and a note.

The router does **not** react to one-off anecdotes. Three observations are required before an exact model + effort + execution configuration can affect the quality score. Evidence from other effort/execution configurations of the same model is a weaker fallback and is not used until at least six category-compatible observations exist. Larger samples are shrunk toward neutral and capped so empirical history tunes the registry instead of replacing it.

When feedback has a `task_category`, it is scoped to that type of work. For example, repeated success on `repo_review` can improve a model's score for future repository reviews but does not raise that model's score for `implementation`. Older untagged feedback remains a conservative fallback for backward compatibility.

The router chooses the dominant category from the current workload profile before applying personal evidence. This keeps personalization task-aware rather than turning a generally successful model into the default for every job.

### Automatic Hive telemetry import

Hive already persists the signals the advisor needs instead of requiring the user to type cost and latency by hand:

- session `events.jsonl` contains `llm_turn_complete` events with model, token usage, cache usage, and provider-reported USD cost;
- session `logs/details.jsonl` contains node outcome, exit status, retry count, and node wall-clock latency.

`feedback-import-hive` joins those two sources and appends eligible observations to the advisor feedback JSONL. It is deliberately conservative:

- the registry must recognize every LLM model used by a node;
- a node that changed models mid-run is skipped instead of assigning its combined outcome/cost to one model;
- runtime details are joined only when the node ID has one unambiguous detail record;
- explicit node/judge outcomes take precedence; execution-level success/failure is attributed only when the **full execution** contains one LLM node, even when `--node-id` filters the import;
- `paused` or `escalated` runtime exits are recorded as `partial`, even if a lower-level success flag is true;
- provider cost is summed across LLM turns; a missing/zero provider cost is treated as unknown, not free;
- corrupt/partial JSONL lines are ignored and reported rather than aborting the whole import;
- each imported observation receives `source_id=hive:<execution_id>:<node_id>`, so rerunning the same import is idempotent.

A normal history import defaults to `effort=observed` and `execution=hive_agent_loop`. That evidence can still influence a model after the conservative six-observation cross-config threshold, but it does **not** pretend the trace proves a specific reasoning-effort setting.

For controlled A/B work, import one known node and stamp the exact configuration plus a stable benchmark task ID:

```bash
python -m tools.ai_model_advisor.cli feedback-import-hive \
  --events /path/to/session/events.jsonl \
  --details /path/to/session/logs/details.jsonl \
  --feedback ~/.hive/model-feedback.jsonl \
  --node-id implementation-worker \
  --task-category implementation \
  --task-id endpoint-benchmark-04 \
  --effort medium \
  --execution single
```

Run the command with `--dry-run` first when inspecting a new trace shape. In dry-run mode the importer produces its report but does not modify the feedback store.

### Bulk Hive history import

`feedback-import-hive-root` discovers past session telemetry below a Hive storage root. Discovery follows the SessionStore contract rather than assuming a particular agent name: only paths shaped like `.../sessions/<session_id>/events.jsonl` are accepted. Nested worker-local `events.jsonl` files and unrelated logs are ignored. A sibling `logs/details.jsonl` file is joined when present.

Bulk import is **preview-only by default**. A first pass such as:

```bash
python -m tools.ai_model_advisor.cli feedback-import-hive-root \
  --root ~/.hive \
  --feedback ~/.hive/model-feedback.jsonl \
  --output /tmp/hive-history-preview.md \
  --json-output /tmp/hive-history-preview.json
```

reports discovered sessions, eligible observations, mixed/unknown-model skips, ambiguous runtime-detail joins, unknown outcomes, and corrupt lines without changing feedback. Add `--apply` only after reviewing that report.

Bulk observations namespace their idempotency key with the session ID (`hive:<session_id>:<execution_id>:<node_id>`), so even reused execution IDs across different sessions cannot collide. Repeating `--apply` on the same history therefore does not duplicate evidence.

Bulk history is intentionally imported as `effort=observed` and `execution=hive_agent_loop` unless explicitly overridden. It is suitable for conservative model-level outcome learning. Use the single-session/node importer with explicit `task_id`, effort, and execution mode when building controlled paired efficiency evidence.

### Paired cost and latency evidence

Absolute elapsed time and spend are **not** compared across unrelated tasks. A five-second one-line fix is not evidence that a configuration is more efficient than a thirty-minute repository audit.

To make cost/latency actionable, give multiple attempts of the same task the same `task_id`. Efficiency evidence is used only when:

- the candidate configuration successfully completed the task;
- at least one different model/effort/execution configuration successfully completed that same `task_id`;
- the records are in the same named `task_category` when a category is present;
- at least three comparable task IDs exist before the aggregate efficiency score can affect routing.

For each shared task ID, the router compares the candidate's median latency/cost with the median of successful peer configurations. Relative differences are capped per task, then shrunk by sample count. The resulting efficiency adjustment is smaller than the outcome-quality adjustment and is also bounded inside the total empirical score.

Failed fast attempts never receive an efficiency reward. Unpaired latency/cost records remain useful historical data but have zero routing effect until a comparable peer run exists.

This design intentionally favors repeated A/B-style evidence over anecdotal absolute numbers.

## Recommendation dimensions

The classifier estimates coding, reasoning, agentic behavior, ambiguity, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale. The router deliberately applies an overkill penalty so a frontier model at maximum effort is not the default for routine work.
