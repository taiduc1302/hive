# AI Model Advisor v0.40 — Cross-Target Stability

v0.39 introduced matched A/B experiments between:

- `hive_agent_loop`: one controlled no-tool AgentLoop turn;
- `hive_agent_loop_tool`: the same provider/model/effort plus one deterministic local tool lifecycle.

v0.40 adds a stability layer on top of the feedback already produced by those runs. It does not call a provider and never mutates routing or Hive configuration.

## Why

A single latency or cost delta is not enough evidence for execution overhead. Queueing, cache state, provider variance, and transient failures can make one pair misleading.

The stability report therefore requires repeated matched task IDs and evaluates both the center and spread of the overhead distribution.

## Command

```bash
python -m tools.ai_model_advisor.cross_target_stability \
  --plan cross-target-plan.json \
  --experiment-id <experiment-id> \
  --feedback feedback.jsonl \
  --output cross-target-stability.md \
  --json-output cross-target-stability.json
```

Optional controls:

```text
--min-pairs 3
--max-mad 0.25
--max-failure-rate-delta 0.20
```

## Matching rules

Only records with all of the following are used:

1. exact saved `model_id + effort + execution_mode` identity for the side;
2. the same `task_id` on A and B;
3. the expected benchmark source IDs:
   - `benchmark:<experiment_id>:<task_id>:a`
   - `benchmark:<experiment_id>:<task_id>:b`.

Duplicate side/task records fail closed.

Latency and cost overhead are computed only when both sides succeeded. Failures are retained separately in the failure-rate comparison.

## Metrics

For each successful matched pair:

```text
overhead ratio = (B - A) / A
```

The report includes:

- matched pair count;
- successful latency/cost pair counts;
- median latency overhead;
- mean latency overhead;
- latency median absolute deviation (MAD);
- median/mean cost overhead and MAD;
- failure rate for each side;
- failure-rate delta `B - A`;
- per-task pair rows for auditability.

Positive overhead means side B is slower or more expensive. Negative overhead means side B is faster or cheaper.

## States

### `insufficient_evidence`

Returned when the report does not yet have the required number of matched pairs or successful latency pairs.

### `unstable`

Returned when enough evidence exists but at least one guardrail fails:

- latency MAD is above the configured threshold;
- cost MAD is above the threshold when enough cost pairs exist;
- absolute failure-rate delta exceeds the configured threshold.

### `stable_overhead`

Returned only when the minimum matched evidence exists and the configured stability guardrails pass.

This state is still analytical evidence only. It does **not** automatically change routing, promotion decisions, or Hive configuration.

## Safety boundary

The report sets:

```json
{"automatic_routing_mutation": false}
```

v0.40 measures repeatability. It does not convert execution-overhead observations into an automatic policy edit.
