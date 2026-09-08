# AI Model Advisor — Configuration-Aware Routing

Version 0.4 changes the router from “pick one heuristic effort/mode per model” to “score every allowed effort/execution configuration inside each model, then keep the best configuration for that model.”

This matters because personal evidence can now change not only **which model** wins, but also **how that model should be run**.

## Candidate hierarchy

For each model in the verified registry, the router evaluates the Cartesian product of:

- the model's supported reasoning efforts;
- the model's supported execution/orchestration modes.

The router does **not** return the whole Cartesian product to the user. It selects the best configuration inside each model first, then ranks those model winners against one another. This preserves model diversity in top-N recommendations and the routing matrix.

Example:

```text
GPT-5.6 Terra
  none / single
  low / single
  medium / single      <- static workload prior
  high / single        <- can win after repeated exact evidence
  xhigh / single
  max / single
  ... same efforts under chatgpt_work

=> one best Terra configuration competes with the best Sol, Luna, Astra, Sonnet, Opus, and Fable configurations.
```

## Static configuration prior

The previous effort/execution heuristic is still useful. It now acts as a **prior**, not a hard choice.

The heuristic-preferred configuration receives a zero prior adjustment. Other configurations receive a negative adjustment based on how far they deviate from that workload-fit baseline.

Higher-than-preferred effort is penalized more when cost/latency sensitivity is high. Lower-than-preferred effort is penalized more as workload difficulty rises.

Deeper-than-preferred orchestration is penalized more when cost/latency sensitivity is high. Shallower-than-preferred orchestration is penalized more when agentic/breadth demand is high.

The total configuration prior is capped. Its purpose is to prevent unexplored configurations from randomly tying the preferred mode, not to make the prior impossible to override.

## Empirical override

Exact repeated evidence can overcome the prior.

For example, if the static workload prior prefers:

```text
gpt-5.6-terra / medium / single
```

but repeated category-matched feedback shows:

```text
gpt-5.6-terra / high / single
```

performing better, the latter configuration can win inside Terra even though it starts with a small negative configuration prior.

The same mechanism applies to execution mode. Repeated exact evidence can move a model from `single` to `subagents`, `dynamic_workflow`, `ultracode`, or another supported mode when the empirical gain is large enough.

## Exact vs cross-config evidence

Exact evidence and same-model fallback evidence are intentionally not equal.

- **Exact configuration evidence:** full quality adjustment after the 3-observation threshold.
- **Same-model cross-config fallback:** starts only after 6 category-compatible observations and is weighted at **35%** of the equivalent exact adjustment.

This prevents six successful `high/subagents` runs from giving `medium/single` almost the same bonus and undoing the ability to learn a real configuration preference.

The 35% fallback factor is defined in the same policy source as the 3/6/3 thresholds and is surfaced in `feedback-report`.

## Score decomposition

Each recommendation exposes these layers:

```text
model_score
+ configuration_adjustment
= base_score

quality_adjustment
+ efficiency_adjustment
= raw_empirical_adjustment

raw empirical adjustment
-> bounded empirical_adjustment

base_score
+ empirical_adjustment
= final score
```

The Markdown report shows the workload heuristic's preferred effort/execution mode alongside the actually selected configuration. When personal evidence causes a deviation, the explanation says that the empirical evidence overrode the static configuration prior.

## Why token count is not part of the configuration prior

A higher reasoning effort can use more tokens and still be the correct choice because it avoids retries or failures. Likewise, an orchestrated execution can be more expensive but produce a much better outcome on broad parallelizable work.

Token/cache/Hive-credit telemetry remains diagnostic-only. Configuration learning currently uses outcome/retry evidence plus paired same-task cost/latency evidence. A future token-efficiency signal should be added only with the same task-normalization discipline.

## Safety properties

Configuration-aware routing keeps the existing guardrails:

- category-tagged evidence cannot leak to another named category;
- unpaired absolute latency/cost does not affect routing;
- failed fast attempts do not receive efficiency rewards;
- mixed-model Hive nodes are not attributed to one model;
- exact evidence needs repeated observations;
- cross-config fallback is delayed and discounted;
- total empirical adjustment remains capped;
- only one winning configuration per model is exposed in normal top-N recommendations.
