# AI Model Advisor standalone migration

AI Model Advisor feature development is no longer intended to continue inside Hive.

## Current Hive boundary

Hive keeps only the minimal runtime bridge that belongs to Hive itself, including optional model reasoning-effort configuration support and compatibility behavior merged through PR #30.

## Standalone source

The standalone extraction snapshot is staged on:

`migration/ai-model-advisor-standalone-v0.1`

Snapshot commit:

`532277ecc296136104e843ce92a002e23922e9e7`

It was assembled from the internal Advisor v0.41 development line and contains Advisor-owned source, tests, documentation, Skill files, standalone packaging metadata and standalone CI without the Hive core tree.

## Development policy

Do not add new Advisor routing, experiment, leaderboard, promotion, canary or rollback features to Hive core.

Future Advisor work belongs in the standalone `ai-model-advisor` repository. Hive should interact with it through a narrow integration boundary.
