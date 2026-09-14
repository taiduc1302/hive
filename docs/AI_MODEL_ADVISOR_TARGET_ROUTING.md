# AI Model Advisor: Target-Aware Routing

## Why this exists

The generic Advisor router answers a capability question: which model, reasoning effort, and execution mode best fit the workload if host availability is ignored.

That answer is useful for planning, but it can name an execution mode that a concrete Advisor runner cannot prove or execute. For example, a broad parallel workload can legitimately prefer `ultracode` in the generic Anthropic ranking while the current trusted `hive` and `provider_api` adapters only accept `single` execution.

Target-aware routing closes that gap **before** experiment preflight.

## Contract

Use `recommend_for_target(...)` when the recommendation is intended for a known Advisor execution target:

```python
from tools.ai_model_advisor import ModelRegistry, RecommendationEngine, recommend_for_target

engine = RecommendationEngine(ModelRegistry())
recommendations = recommend_for_target(
    engine,
    workload,
    "hive",
    providers=["anthropic"],
    top_n=3,
)
```

Or use the standalone CLI:

```bash
python -m tools.ai_model_advisor.target_recommend \
  --profile model-advisor-output/profile.json \
  --target hive \
  --output model-advisor-output/recommendation.hive.md \
  --json-output model-advisor-output/recommendation.hive.json
```

Supported target names come from `tools.ai_model_advisor.execution_targets`; the router does not maintain a second capability table.

## Ranking behavior

Target-aware routing does **not** rank the generic recommendations and then discard unsupported rows. It narrows the configuration space first and recomputes each model's preferred execution mode inside the executable subset.

That distinction matters. If the generic workload prior prefers `ultracode` but the selected target can only run `single`, `single` becomes the target-local preferred mode rather than receiving a penalty for failing to use an unavailable control.

The resulting ranking still uses the same model-fit score, effort prior, personal feedback adjustments, paired cost/latency evidence, confidence calculation, and registry/provider filters as the generic router.

## Generic vs target-aware output

Use generic routing when answering questions such as:

- Which configuration is theoretically best for this workload?
- Is orchestration justified?
- Which host or product should I consider using?

Use target-aware routing when answering:

- What should I run through Hive right now?
- What can the direct provider adapter execute?
- Which configuration should feed a controlled experiment on a known target?

A target-aware recommendation is still not execution proof. Preflight and the adapter's `applied_configuration` evidence remain mandatory for controlled experiments.

## Current target limitation

At v0.13.0, both built-in trusted experiment targets advertise `execution_mode=single` in their execution-target contracts. Hive itself contains richer agent/orchestration machinery, but the Advisor must not treat host code presence as proof that the experiment adapter can faithfully apply and attest those controls. Add broader modes to target routing only when the corresponding adapter contract and evidence path are implemented.
