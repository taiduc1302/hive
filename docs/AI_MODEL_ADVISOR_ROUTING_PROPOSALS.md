# AI Model Advisor Routing Proposals

Routing proposals connect the Advisor's current routing matrix with the empirical leaderboard. They are deliberately **review-only**: the command can recommend a change, but it never edits router policy or invokes a provider.

## Usage

```bash
python -m tools.ai_model_advisor.routing_proposals \
  --routing-matrix model-advisor-output/routing-matrix.json \
  --feedback feedback.jsonl \
  --output model-advisor-output/routing-proposals.md \
  --json-output model-advisor-output/routing-proposals.json
```

## Actions

- `propose_change`: controlled evidence promotes a configuration that differs from the current primary route;
- `keep`: the current primary already matches the promoted configuration, or the evidence says the leaders are too close;
- `collect_more`: a meaningful gap exists but balanced evidence is not yet strong enough;
- `insufficient_evidence`: no decision-grade controlled comparison exists yet.

Every proposal carries the current route, candidate configuration, confidence, leaderboard margins, evidence status, and review flag.

## Safety boundary

`safe_to_apply` is always `false`.

A `propose_change` record means **review this change**, not **apply this change**. Production routing changes remain a separate explicit edit followed by tests and CI. Historical `observed / hive_agent_loop` evidence may appear elsewhere in Advisor reports, but it cannot become the controlled winner that drives a routing proposal.
