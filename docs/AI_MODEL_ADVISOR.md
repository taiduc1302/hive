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

There is no hidden API in this project for reading a user's entire ChatGPT profile/history. The advisor analyzes only data explicitly available to it: a supplied ChatGPT export, generic activity JSON, GitHub events, or explicitly authorized sources.

## CLI

```bash
python -m tools.ai_model_advisor.cli profile --input tools/ai_model_advisor/sample_activity.json --output /tmp/workload.json
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --output /tmp/recommendation.md
python -m tools.ai_model_advisor.cli matrix --input tools/ai_model_advisor/sample_activity.json --output /tmp/routing-matrix.md --json-output /tmp/routing-matrix.json
python -m tools.ai_model_advisor.cli matrix --chatgpt-export /path/to/conversations.json --feedback ~/.hive/model-feedback.jsonl --output /tmp/personal-routing.md
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --feedback ~/.hive/model-feedback.jsonl --output /tmp/personalized.md
python -m tools.ai_model_advisor.cli feedback-add --feedback ~/.hive/model-feedback.jsonl --provider anthropic --model claude-sonnet-5 --effort high --execution single --outcome success --retries 0 --task-category implementation
python -m tools.ai_model_advisor.cli scan --baseline /tmp/source-baseline.json --write-baseline /tmp/source-baseline.json --output /tmp/source-scan.md --json-output /tmp/source-scan.json
```

## Routing matrix

`matrix` groups the supplied activity by recognized task category and builds a separate workload profile for each category. One activity may contribute to multiple relevant categories (for example, a repository-wide debugging task can be both `debugging` and `repo_review`), but each routing row is forced to its own category before personal feedback is applied.

The Markdown output includes the primary model/effort/execution configuration plus alternatives and a switch-up trigger. The scheduled GitHub Action publishes the overall recommendation, task routing matrix, and source-change scan directly in the Actions Job Summary and also stores Markdown/JSON copies in the workflow artifact.

## Personal feedback policy

Feedback is JSONL and stays local unless the user deliberately commits/uploads it. A record can include outcome (`success`, `partial`, `failure`), retries, latency, cost, task category, and a note.

The router does **not** react to one-off anecdotes. Fewer than three matching observations have zero scoring effect. Larger samples are shrunk toward neutral and capped so empirical history tunes the registry instead of replacing it.

When feedback has a `task_category`, it is scoped to that type of work. For example, repeated success on `repo_review` can improve a model's score for future repository reviews but does not raise that model's score for `implementation`. Older untagged feedback remains a conservative fallback for backward compatibility.

The router chooses the dominant category from the current workload profile before applying personal evidence. This keeps personalization task-aware rather than turning a generally successful model into the default for every job.

## Recommendation dimensions

The classifier estimates coding, reasoning, agentic behavior, ambiguity, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale. The router deliberately applies an overkill penalty so a frontier model at maximum effort is not the default for routine work.
