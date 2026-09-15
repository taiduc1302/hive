# AI Model Advisor — Experiment Impact

`experiment-evaluate` answers **which side won the fixed A/B experiment**. `experiment-impact` answers the separate question: **does the current live router already select that winning configuration?**

The distinction matters because a controlled A/B result is one piece of evidence. The live router still considers the full model field, static capability fit, configuration prior, other empirical history, provider scope, and current registry availability.

## Run the impact check

Use the same saved JSON plan and feedback store used for evaluation:

```bash
python -m tools.ai_model_advisor.cli experiment-impact \
  --plan /tmp/model-experiments.json \
  --feedback ~/.hive/model-feedback.jsonl \
  --output /tmp/model-impact.md \
  --json-output /tmp/model-impact.json
```

The command is **read-only**. It never edits feedback, changes registry data, or writes routing policy.

## What it compares

For every experiment in the saved plan, impact analysis:

1. re-evaluates the fixed experiment from current feedback;
2. reconstructs the planning-time workload profile for that task category;
3. runs the current personalized router for that workload;
4. if the experiment has a directional winner, re-scores the exact winner and loser under the current registry + feedback;
5. compares the experiment winner with the current router primary;
6. reports the current score decomposition and the winner-to-current score gap.

Undecided experiments are reported as `not_decided`; they are **not** treated as router conflicts.

## Routing scope is part of the experiment

`experiment-plan` persists the routing scope used to create the comparison:

```json
{
  "routing_scope": {
    "providers": ["anthropic"],
    "include_limited": false
  }
}
```

Impact analysis inherits the saved provider scope when no explicit provider override is supplied. This prevents an Anthropic-only experiment from being incorrectly labeled a gap simply because a global router would currently choose an OpenAI model.

An explicit `--provider openai` or `--provider anthropic` on `experiment-impact` overrides the saved provider list for the diagnostic comparison. Use that only when you intentionally want to ask a different routing-scope question.

## Impact statuses

A decided experiment can produce:

- `aligned` — the live router selects the exact experiment winner;
- `still_on_experiment_loser` — the live router still selects the exact losing configuration;
- `same_winner_model_different_configuration` — the winning model is selected, but with a different effort or execution mode;
- `same_loser_model_different_configuration` — the router remains on the losing model but with a different configuration;
- `different_primary` — another model/configuration outranks both experiment sides;
- `winner_unavailable` — the saved winning configuration no longer exists in the current registry;
- `no_router_candidate` — no candidate exists under the active provider/availability scope;
- `not_decided` — the experiment itself has not produced a directional winner.

## Score-gap interpretation

For a decided experiment the report includes the experiment winner re-scored under the **current** router state:

- model capability score;
- configuration prior adjustment;
- current empirical adjustment;
- final score;
- winner minus current-router score.

A negative winner-to-current gap explains why the live router has not adopted the A/B winner. The report does not assume that this is a bug: the current router may have stronger evidence for another model, or the controlled experiment may have tested a narrower question than the global category route.

## Machine-readable next actions

Impact analysis never applies the result. It emits an advisory `next_action`:

- aligned winner → `observe_and_rerun_later`;
- decided but router-disagrees → `review_router_gap` with the current score gap;
- winner no longer available → `refresh_registry_or_plan`;
- no candidate under current scope → `review_provider_filters_or_registry`;
- experiment not decided → forwards the experiment evaluator's next action.

The intended loop is therefore:

```text
activity
  -> route
  -> experiment-plan
  -> collect paired outcomes
  -> experiment-evaluate
  -> experiment-impact
  -> review winner/router gap
  -> rerun router
```

A controlled experiment can inform policy, but the Advisor deliberately keeps the final policy change reviewable instead of silently turning one A/B result into a permanent routing override.
