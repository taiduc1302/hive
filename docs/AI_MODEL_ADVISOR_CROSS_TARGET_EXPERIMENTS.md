# AI Model Advisor: Cross-Target AgentLoop Experiments

AI Model Advisor v0.39 compares the execution overhead of two controlled Hive AgentLoop targets while holding the model configuration constant.

## What is compared

The derived benchmark clones one saved provider/model/effort configuration onto both sides:

- **A**: `hive_agent_loop` — one no-tool AgentLoop turn;
- **B**: `hive_agent_loop_tool` — one deterministic local tool call and two AgentLoop LLM turns.

Only the execution target/mode changes. This makes paired latency/cost evidence interpretable as execution overhead instead of mixing model-quality changes into the same comparison.

The source side can be selected with `--source-side A|B`; its provider/model/effort becomes the baseline for both derived sides.

## Preview first

```bash
python -m tools.ai_model_advisor.cross_target_run_cli \
  --plan experiment-plan.json \
  --experiment-id <id> \
  --feedback feedback.jsonl \
  --task "Return exactly 42." \
  --derived-plan-output overhead-plan.json \
  --json-output overhead-preview.json
```

Preview performs no adapter or provider call and does not mutate feedback.

The derived plan records an immutable per-side target binding under `cross_target_execution`. Apply-time dispatch resolves only the canonical runner module declared by the execution-target catalog for each side.

## Apply with deterministic acceptance

```bash
python -m tools.ai_model_advisor.cross_target_run_cli \
  --plan experiment-plan.json \
  --experiment-id <id> \
  --feedback feedback.jsonl \
  --task "Return exactly 42." \
  --expected-output-file expected.txt \
  --apply \
  --output overhead-run.md \
  --json-output overhead-run.json
```

Apply requires an expected-output fixture. Both adapters still report transport/lifecycle completion as provisional; the deterministic judge independently converts each result to success/failure.

Both sides are validated before the pair is appended. Infrastructure failure on either side writes no benchmark pair.

## Evidence reuse

The cross-target runner intentionally reuses the standard experiment machinery:

- one shared `task_id`;
- alternating A/B execution order;
- source-id duplicate protection;
- atomic pair append;
- category isolation;
- latency/cost telemetry;
- paired-efficiency scoring.

After at least the normal paired-evidence threshold, the existing router can therefore measure whether the tool lifecycle adds enough cost/latency to matter for that task category.

## Safety boundary

This is not a general autonomous-agent benchmark. The tool side exposes only the deterministic in-process `advisor_constant` fixture from v0.38. There is no MCP, shell, filesystem, browser, network, colony, subagent, ChatGPT Work, or ultracode capability.

The cross-target runner has no arbitrary runner override. Side A/B runners are resolved from the execution-target catalog so a saved target binding cannot silently be executed through a different adapter.
