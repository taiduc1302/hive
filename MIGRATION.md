# Standalone extraction

## Source

The standalone snapshot is built from:

- source repository: `taiduc1302/hive`;
- source line: `feature/ai-model-advisor-v0.41`;
- source commit: `c965e02748dbcaa915abf314ed77df366da3dbc4`;
- merged Hive bridge baseline: PR #30.

## What stays in Hive

Only the runtime bridge that genuinely belongs to Hive should remain coupled to Hive core, primarily optional `reasoning_effort` configuration passthrough and compatibility tests.

## What moves here

Advisor-owned behavior moves to the standalone project:

- model registry and source monitoring;
- workload profiling;
- recommendation and routing matrix;
- feedback and telemetry analysis;
- controlled experiments;
- target-aware execution contracts;
- empirical leaderboard;
- routing proposals;
- promotion/canary/rollback evidence workflow;
- provider adapters;
- Hive integration adapters as optional integrations;
- Advisor Skill and documentation.

## Versioning

Hive-era internal development reached 0.41.0. Standalone releases restart at 0.1.0.

## Package-path cleanup

The first extraction intentionally preserves the proven Python import path `tools.ai_model_advisor` to minimize migration risk. A later standalone-only refactor can move the package to `src/ai_model_advisor` with compatibility shims and its own focused PR.

## CI split

Standalone core CI runs without Hive installed. Hive runtime compatibility remains a separate integration concern and should be exercised against the Hive repository or an explicit Hive dependency.
