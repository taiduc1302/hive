# AI Model Advisor

`tools/ai_model_advisor` is a model watcher and workload router for Hive.

It answers three questions separately:

1. **What models and execution modes are current?** Scan official provider documentation and flag model/mode signals that changed.
2. **What should I use for my work?** Convert recent activity into a workload profile and recommend a model + reasoning effort + execution mode.
3. **What actually works for me?** Record real outcomes and use repeated evidence to personalize later recommendations.

## Registry policy

The committed registry is a verified snapshot, not a permanent source of truth. Every model entry points to official provider sources. A scheduled scan detects drift.

- Official provider docs and release notes are authoritative.
- Community posts are discovery signals only until officially confirmed.
- Limited-rollout models are excluded from normal recommendations by default.
- Model, reasoning effort, and orchestration mode are stored separately.

## Privacy boundary

There is no hidden API in this project for reading a user's entire ChatGPT profile/history. The advisor analyzes only data explicitly available to it: a supplied ChatGPT export, generic activity JSON, GitHub events, or explicitly authorized sources.

## CLI

```bash
python -m tools.ai_model_advisor.cli profile --input tools/ai_model_advisor/sample_activity.json --output /tmp/workload.json
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --output /tmp/recommendation.md
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --feedback ~/.hive/model-feedback.jsonl --output /tmp/personalized.md
python -m tools.ai_model_advisor.cli feedback-add --feedback ~/.hive/model-feedback.jsonl --provider anthropic --model claude-sonnet-5 --effort high --execution single --outcome success --retries 0
python -m tools.ai_model_advisor.cli scan --output /tmp/source-scan.md --json-output /tmp/source-scan.json
```

## Personal feedback policy

Feedback is JSONL and stays local unless the user deliberately commits/uploads it. A record can include outcome (`success`, `partial`, `failure`), retries, latency, cost, task category, and a note.

The router does **not** react to one-off anecdotes. Fewer than three matching observations have zero scoring effect. Larger samples are shrunk toward neutral and capped so empirical history tunes the registry instead of replacing it.

## Recommendation dimensions

The classifier estimates coding, reasoning, agentic behavior, ambiguity, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale. The router deliberately applies an overkill penalty so a frontier model at maximum effort is not the default for routine work.
