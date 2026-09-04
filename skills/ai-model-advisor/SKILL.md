---
name: ai-model-advisor
description: Track current AI model, reasoning-effort, coding-agent, and orchestration changes and recommend the best configuration for a user's actual workload. Use when asked which ChatGPT/OpenAI/Codex or Claude/Claude Code model or mode to use, when comparing high/xhigh/max/ultracode-style modes, when reviewing recent AI releases, when analyzing GitHub or explicit ChatGPT activity to set defaults, or when preparing a recurring model-selection review.
---

# AI Model Advisor

## 1. Verify freshness first

When the request depends on what is current, inspect official provider documentation/release notes before recommending anything. Read `references/source-policy.md` for source order and official URLs. Never promote community-only names or rumored modes without official confirmation.

## 2. Keep three decisions separate

Always distinguish model, reasoning effort, and execution/orchestration mode. Do not describe an orchestration mode as merely a stronger effort level.

## 3. Analyze workload evidence

Use only activity legitimately available in the current environment: current conversation, authorized connectors, supplied files, or an explicit ChatGPT export. Do not claim access to a hidden entire-history API. If deterministic summarization is useful, run `scripts/summarize_activity.py`.

Estimate coding intensity, reasoning depth, ambiguity, long-running/agentic work, breadth, parallelism, latency sensitivity, and cost sensitivity on a 1-5 scale.

## 4. Choose by measured need

Prefer the cheapest/faster configuration likely to succeed. Escalate model or effort when ambiguity, error cost, breadth, or long-horizon autonomy rises. Prefer orchestration when work has independent units or verification stages; do not use multi-agent execution for a single tightly coupled reasoning problem just because it sounds harder.

## 5. Return an actionable recommendation

Use `references/output-pattern.md`. Give one primary configuration, one cheaper/faster fallback, one escalation configuration only if justified, and the exact trigger to switch between them. Include confidence and availability caveats. Cite official sources when current web data was used.

## 6. Recurring review

For ongoing monitoring, check official provider docs/release notes, compare material changes with the previous review, inspect only explicitly available activity, and change defaults only when evidence supports it.
