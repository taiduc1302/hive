# ChatGPT setup: Skill + Scheduled review

The repository includes a ChatGPT Skill at `skills/ai-model-advisor/`.

## Recommended setup

1. Package/install the `ai-model-advisor` Skill.
2. Use it for on-demand questions such as “Which Claude Code mode should I use for this repo audit?”
3. Add a recurring ChatGPT Scheduled Task for a weekly model/activity review.

Suggested scheduled-task instruction:

> Check official OpenAI and Anthropic model/release documentation for material changes since the previous review. Then review the GitHub activity and other activity sources that are explicitly available to you. Recommend any changes to my default model/effort/execution-mode choices. Separate confirmed official changes from unverified reports. If nothing material changed, say so briefly and do not invent a recommendation change.

A weekly cadence is usually enough for the personalized review. The GitHub Action scans official sources daily so releases are visible sooner.

## Privacy

The repository cannot silently create a Custom GPT or read all ChatGPT conversations through an undocumented endpoint. Use explicit exports/connectors for activity that is not already available in the active ChatGPT context.
