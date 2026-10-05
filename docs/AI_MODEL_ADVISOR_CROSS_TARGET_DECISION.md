# AI Model Advisor v0.41 — Cross-Target Operator Decision

v0.40 establishes whether AgentLoop execution overhead is repeatable.

v0.41 converts that stability artifact into a **bounded operator recommendation** for the same benchmark class. It does not rerun the experiment, call a provider, change routing, or edit Hive configuration.

## Input

The command consumes one v0.40 stability JSON artifact:

```bash
python -m tools.ai_model_advisor.cross_target_decision \
  --stability cross-target-stability.json \
  --output cross-target-decision.md \
  --json-output cross-target-decision.json
```

The report is intentionally specific to the canonical comparison:

```text
A = hive_agent_loop
B = hive_agent_loop_tool
```

Reversed or unrelated execution targets fail closed.

## Default policy

```text
max latency overhead:       15%
max cost overhead:          15%
material reliability gain:  10 percentage points
```

These are decision thresholds, not automatic routing rules.

## Decisions

### `collect_more`

The v0.40 report is still `insufficient_evidence`.

No execution-target preference should be inferred yet.

### `investigate_instability`

The v0.40 report is `unstable`.

There may be enough observations to see a signal, but variance or failure-rate movement is too large to make a target choice.

### `prefer_no_tool_for_equivalent_tasks`

The tool-enabled target has stable material latency or cost overhead and does **not** show a material reliability advantage.

This applies only to tasks represented by the controlled benchmark.

### `tool_overhead_acceptable`

The stable tool-enabled overhead remains inside both configured budgets.

This does not mean tool mode is globally better; it means the measured overhead is within the operator's tolerance for equivalent benchmark tasks.

### `manual_tradeoff_review`

The tool-enabled target exceeds an overhead budget but also has a material failure-rate advantage.

The tool intentionally preserves this as a human tradeoff instead of collapsing cost/latency and reliability into one opaque score.

## Safety boundary

Every v0.41 result includes:

```json
{
  "scope": "equivalent_benchmark_tasks_only",
  "safe_to_auto_apply": false,
  "automatic_routing_mutation": false,
  "automatic_hive_config_mutation": false
}
```

The decision artifact is advisory evidence. It is not an execution or promotion command.

## Suggested workflow

```text
v0.39 matched cross-target runs
        ↓
v0.40 repeatability / stability report
        ↓
v0.41 bounded operator decision
        ↓
manual review
```

Model ranking inside an executable target remains the job of `target_recommend`. v0.41 answers a different question: whether the extra tool-enabled AgentLoop overhead is justified for the controlled class of equivalent tasks.
