# AI Model Advisor

`tools/ai_model_advisor` is a dependency-free model watcher and workload router for Hive.

It answers two questions separately:

1. **What models and execution modes are current?** Scan official provider documentation and flag model/mode signals that changed.
2. **What should I use for my work?** Convert recent activity into a workload profile and recommend a model + reasoning effort + execution mode.

## Why this is separate from Hive's runtime

Hive is an agent harness. The advisor is a policy layer. Keeping it separate means it can be used from Hive, Claude Code, Codex, ChatGPT Work, a scheduled GitHub Action, or a ChatGPT Skill without coupling recommendation logic to one runtime.

## Registry policy

The committed registry is a verified snapshot, not a permanent source of truth. Every model entry points to official provider sources. A scheduled scan detects drift.

- Official provider docs and release notes are authoritative.
- Community posts are discovery signals only until officially confirmed.
- Limited-rollout models are excluded from normal recommendations by default.
- Model, reasoning effort, and orchestration mode are stored separately.

## Privacy boundary

There is no hidden API in this project for reading a user's entire ChatGPT profile/history. The advisor analyzes only data explicitly available to it:

- a ChatGPT `conversations.json` export supplied by the user;
- generic activity JSON;
- GitHub public events by default;
- connected/private sources only when the runtime has explicit authorization.

## CLI

```bash
python -m tools.ai_model_advisor.cli profile --input tools/ai_model_advisor/sample_activity.json --output /tmp/workload.json
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --output /tmp/recommendation.md --json-output /tmp/recommendation.json
python -m tools.ai_model_advisor.cli recommend --profile /tmp/workload.json --provider anthropic --output /tmp/claude.md
python -m tools.ai_model_advisor.cli profile --chatgpt-export /path/to/conversations.json --output /tmp/chatgpt-workload.json
python -m tools.ai_model_advisor.cli profile --github-user taiduc1302 --output /tmp/github-workload.json
python -m tools.ai_model_advisor.cli scan --output /tmp/source-scan.md --json-output /tmp/source-scan.json
```

## Recommendation dimensions

The classifier estimates coding, reasoning, agentic behavior, ambiguity, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale. The router deliberately applies an overkill penalty so a frontier model at maximum effort is not the default for routine work.
