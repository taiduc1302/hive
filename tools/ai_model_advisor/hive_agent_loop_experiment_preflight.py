from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .execution_targets import (
    configuration_blockers,
    profile_for_host,
    target_binding_blockers,
)
from .experiment_run import ExperimentRunnerError, find_experiment
from .experiment_target import target_summary
from .registry import ModelRegistry
from .runtime_capabilities import build_capability_report


def _load_plan(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExperimentRunnerError("Experiment plan root must be a JSON object")
    return data


def _side_configuration(pair: dict[str, Any], side: str) -> dict[str, Any]:
    key = "primary" if side == "A" else "challenger"
    config = pair.get(key)
    if not isinstance(config, dict):
        raise ExperimentRunnerError(
            f"Experiment side {side} must be a configuration object"
        )
    return config


def _model_lookup(registry: ModelRegistry) -> dict[tuple[str, str], Any]:
    return {(model.provider, model.model_id): model for model in registry.models}


def evaluate_hive_agent_loop_preflight(
    plan: dict[str, Any],
    experiment_id: str,
    *,
    capability_report: dict[str, Any] | None = None,
    registry: ModelRegistry | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    pair = find_experiment(plan, experiment_id)
    capabilities = capability_report or build_capability_report()
    models = _model_lookup(registry or ModelRegistry())
    env = environ if environ is not None else os.environ
    profile = profile_for_host("hive_agent_loop")
    raw_target = plan.get("execution_target")
    target = target_summary(plan)

    target_blockers = target_binding_blockers(
        raw_target if isinstance(raw_target, dict) else None,
        profile,
        allow_unbound=False,
    )
    blockers = [f"target: {reason}" for reason in target_blockers]
    runtime_ready = bool(
        capabilities.get("transport", {}).get("agent_loop_evidence_ready")
    )
    sides: dict[str, dict[str, Any]] = {}

    for side in ("A", "B"):
        config = _side_configuration(pair, side)
        provider = str(config.get("provider") or "")
        model_id = str(config.get("model_id") or "")
        effort = str(config.get("effort") or "")
        execution_mode = str(config.get("execution_mode") or "")
        side_blockers = configuration_blockers(config, profile)

        model = models.get((provider, model_id))
        if model is None:
            side_blockers.append(
                f"registry does not contain provider/model "
                f"{provider or 'missing'}/{model_id or 'missing'}"
            )
        elif effort not in model.efforts:
            side_blockers.append(
                f"effort={effort} is not declared for registry model {model_id}; "
                f"allowed: {', '.join(model.efforts)}"
            )

        credential_name = profile.credential_env_by_provider.get(provider)
        if credential_name and not env.get(credential_name):
            side_blockers.append(f"{credential_name} is not set")
        if not runtime_ready:
            side_blockers.append(
                "Hive AgentLoop lifecycle + wire-evidence runtime is not ready"
            )

        blockers.extend(f"{side}: {reason}" for reason in side_blockers)
        sides[side] = {
            "configuration": {
                "provider": provider,
                "model_id": model_id,
                "effort": effort,
                "execution_mode": execution_mode,
            },
            "credential": credential_name,
            "ready": not side_blockers,
            "blockers": side_blockers,
        }

    return {
        "schema_version": 1,
        "host": profile.host,
        "experiment_id": experiment_id,
        "category": pair.get("category"),
        "kind": pair.get("kind"),
        "execution_target": target,
        "target_profile": profile.as_dict(),
        "target_ready": not target_blockers,
        "runtime_ready": runtime_ready,
        "ready": not blockers,
        "sides": sides,
        "blockers": blockers,
        "runtime": capabilities,
        "evidence_boundary": (
            "Preflight proves target binding, registry/effort compatibility, "
            "credential presence, and local AgentLoop plumbing only. A real --apply "
            "run must still prove exact model/effort on Hive's post-transform request "
            "body plus one no-tool AgentLoop turn and an implicit ACCEPT lifecycle."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive AgentLoop Experiment Preflight",
        "",
        f"Experiment: `{report['experiment_id']}`",
        f"Category: **{report.get('category') or 'unknown'}**",
        f"Kind: **{report.get('kind') or 'unknown'}**",
        f"Runtime evidence ready: **{'yes' if report['runtime_ready'] else 'no'}**",
        f"Ready for Hive AgentLoop adapter: **{'yes' if report['ready'] else 'no'}**",
        "",
        "## Sides",
        "",
    ]
    for side in ("A", "B"):
        entry = report["sides"][side]
        config = entry["configuration"]
        lines.append(
            f"- **{side}**: `{config['provider']} / {config['model_id']} / "
            f"{config['effort']} / {config['execution_mode']}` — "
            f"**{'ready' if entry['ready'] else 'blocked'}**"
        )
        lines.extend(f"  - {reason}" for reason in entry["blockers"])
    if report["blockers"]:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {reason}" for reason in report["blockers"])
    lines.extend(["", "## Evidence boundary", "", report["evidence_boundary"], ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight one saved experiment against the controlled Hive AgentLoop adapter."
        )
    )
    parser.add_argument("--plan", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_hive_agent_loop_preflight(
        _load_plan(args.plan),
        args.experiment_id,
    )
    print(
        json.dumps(report, indent=2, sort_keys=True)
        if args.json
        else render_markdown(report),
        end="\n" if args.json else "",
    )
    if args.require_ready and not report["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
