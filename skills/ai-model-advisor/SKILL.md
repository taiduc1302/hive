---
name: ai-model-advisor
description: Track current AI models, reasoning effort, coding-agent and orchestration changes, then recommend and empirically validate the best configuration for a user's real workload. Use when choosing ChatGPT/OpenAI/Codex or Claude/Claude Code model/mode, comparing high/xhigh/max/ultracode-style modes, reviewing AI releases, analyzing GitHub or explicit ChatGPT activity, importing Hive telemetry, auditing learned preferences, planning or running controlled A/B model/effort/execution experiments, comparing repeated task cost/latency, or preparing recurring model-selection reviews.
---

# AI Model Advisor

## 1. Verify freshness first

When the request depends on what is current, inspect official provider documentation/release notes before recommending anything. Read `references/source-policy.md` for source order and official URLs. Never promote community-only names or rumored modes without official confirmation.

## 2. Keep three decisions separate

Always distinguish model, reasoning effort, and execution/orchestration mode. Do not describe an orchestration mode as merely a stronger effort level.

## 3. Analyze workload evidence

Use only activity legitimately available in the current environment: current conversation, authorized connectors, supplied files, an explicit ChatGPT export, or Hive telemetry explicitly provided for analysis. Do not claim access to a hidden entire-history API. If deterministic summarization is useful, run `scripts/summarize_activity.py`.

Estimate coding intensity, reasoning depth, ambiguity, long-running/agentic work, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale. Prefer a per-task routing matrix when the user's work spans materially different categories.

## 4. Choose by measured need

Prefer the cheapest/faster configuration likely to succeed. Escalate model or effort when ambiguity, error cost, breadth, or long-horizon autonomy rises. Prefer orchestration when work has independent units or verification stages; do not use multi-agent execution for a single tightly coupled reasoning problem just because it sounds harder.

Treat personal outcome history as a tuning signal, not ground truth. Require repeated evidence before changing defaults. Do not transfer category-tagged success between unrelated task categories.

## 5. Learn from comparable outcomes

When the Hive Advisor CLI is available, prefer recorded outcomes over manual anecdotes.

For one Hive session, use `feedback-import-hive` against the session `events.jsonl` and optional `logs/details.jsonl`. Use `--dry-run` before writing unfamiliar traces. Skip mixed-model nodes rather than assigning their combined outcome to one model.

For a Hive storage root, use `feedback-import-hive-root`. Treat it as preview-only unless `--apply` is explicitly requested. Discover only canonical `.../sessions/<session_id>/events.jsonl` traces; do not ingest nested worker-local event logs as separate sessions.

After importing history, use `feedback-report` before changing routing defaults. Check observation counts, S/P/F split, retry/cost/latency coverage, exact/cross-config eligibility, paired comparable tasks, and the actual quality/efficiency deltas. If the report cannot explain a non-zero empirical adjustment, do not trust that personalization signal until the discrepancy is resolved.

For controlled A/B comparisons, assign the same stable `task_id` to repeated attempts of the same task and record the exact effort/execution mode. Compare cost/latency only across successful attempts of that same task and category. Never reward a failed attempt merely because it was fast or cheap.

## 6. Run controlled experiments defensibly

When the user wants to validate one model/effort/execution choice against another, generate and keep a fixed `experiment-plan` JSON, then read `references/experiment-runner.md`.

Always preview `experiment-run` before real execution. Preview must launch nothing, write no feedback, and omit benchmark prompt text from preview artifacts while retaining its SHA-256.

Use `--apply` only when real provider/execution work is intended. Require a trusted adapter to echo the exact `applied_configuration`; reject the observation if any requested provider/model/effort/execution field was not actually honored. Treat adapter failures, timeouts, malformed responses, unsupported controls, and configuration mismatches as infrastructure failures rather than model failures.

Use live feedback readiness instead of trusting a potentially stale saved plan. Stop ordinary collection after the required complete A/B threshold unless extra evidence is explicitly desired. Evaluate the same saved plan with `experiment-evaluate`, then inspect `experiment-impact` before changing normal routing defaults.

## 7. Return an actionable recommendation

Use `references/output-pattern.md`. Give one primary configuration, one cheaper/faster fallback, one escalation configuration only if justified, and the exact trigger to switch between them. Include confidence and availability caveats. Cite official sources when current web data was used.

When empirical history materially changes the ranking, state the relevant task category, whether the evidence is outcome-based or paired same-task cost/latency evidence, the sample size, and the applied empirical score delta when available. If the raw empirical sum was capped, say so rather than hiding the clipping.

## 8. Recurring review

For ongoing monitoring, check official provider docs/release notes, compare material changes with the previous review, inspect only explicitly available activity/telemetry, run the feedback audit when personalization is active, and change defaults only when evidence supports it. Preserve prior source fingerprints so temporary provider-page failures do not create false change alerts.
