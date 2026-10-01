# AI Model Advisor: Hive AgentLoop Adapter

v0.36 adds the first controlled execution target that exercises Hive's real
`AgentLoop` instead of only the underlying LiteLLM transport.

## What this target proves

The target is bound as:

- host: `hive_agent_loop`
- adapter: `hive_agent_loop`
- execution mode: `hive_agent_loop`
- external deterministic judge: required

A successful adapter run must prove all of the following before its output is
accepted as experiment evidence:

1. the requested model and reasoning effort survived LiteLLM's provider
   transformation and are present on Hive's captured wire body;
2. the request passed through a real `AgentLoop.execute()` lifecycle;
3. exactly one LLM turn completed;
4. no tools were called;
5. exactly one implicit judge verdict was emitted and it was `ACCEPT`;
6. exactly one AgentLoop start and completion event were emitted.

Transport/lifecycle success is still recorded as `outcome: partial`. Only the
experiment's external deterministic judge may convert the candidate answer into
benchmark success/failure.

## Why the benchmark uses stream_id=judge

A normal Hive queen can park waiting for a human. A normal worker can auto-
escalate after text-only turns. The controlled benchmark deliberately uses
Hive's existing `stream_id="judge"` path so neither behavior contaminates the
measurement. This does not mean the benchmark is using a second judge model:
the AgentLoop's own implicit judge evaluates the one no-tool turn locally and
accepts it when there are no required output keys.

## Isolation

The adapter uses a temporary `HIVE_HOME` and exposes no tools. Provider keys
come from the environment:

- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`

The adapter does not persist captured headers or API keys in result JSON.

## Usage

First bind an experiment plan to the AgentLoop target:

```text
python -m tools.ai_model_advisor.experiment_target \
  --plan experiment-plan.json \
  --host hive_agent_loop \
  --output experiment-plan.hive-agent-loop.json
```

Then run the preflight:

```text
python -m tools.ai_model_advisor.hive_agent_loop_experiment_preflight \
  --plan experiment-plan.hive-agent-loop.json \
  --experiment-id <id> \
  --require-ready
```

Use this runner with `experiment-run --apply`:

```text
python -m tools.ai_model_advisor.hive_agent_loop_adapter
```

The runner consumes the standard experiment-run JSON payload on stdin and emits
the standard result JSON on stdout.

## Evidence boundary

This target measures the real Hive AgentLoop prompt/conversation/lifecycle
overhead for a one-turn, no-tool task. It does **not** claim to measure colony
workers, dynamic workflows, subagents, browser/computer use, ChatGPT Work, or
ultracode. Those remain separate execution targets until a host-specific
adapter can prove their controls and telemetry.
