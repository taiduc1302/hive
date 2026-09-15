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


def _load_plan(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExperimentRunnerError("Experiment plan root must be a JSON object")
    return data


def _side_configuration(pair: dict[str, Any], side: str) -> dict[str, Any]:
    key = "primary" if side == "A" else "challenger"
    config = pair.get(key)
    if not isinstance(config, dict):
        raise ExperimentRunnerError(f"Experiment side {side} must be a configuration object")
    return config


def _model_lookup(registry: ModelRegistry) -> dict[tuple[str, str], Any]:
    return {(model.provider, model.model_id): model for model in registry.models}


def evaluate_provider_experiment_preflight(
    plan: dict[str, Any],
    experiment_id: str,
    *,
    registry: ModelRegistry | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a saved pair for the built-in direct OpenAI/Anthropic adapter."""
    pair = find_experiment(plan, experiment_id)
    models = _model_lookup(registry or ModelRegistry())
    env = environ if environ is not None else os.environ
    profile = profile_for_host("provider_api")
    raw_target = plan.get("execution_target")
    target = target_summary(plan)

    target_blockers = target_binding_blockers(
        raw_target if isinstance(raw_target, dict) else None,
        profile,
        allow_unbound=False,
    )
    blockers = [f"target: {reason}" for reason in target_blockers]
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
                f"registry does not contain provider/model {provider or 'missing'}/{model_id or 'missing'}"
            )
        elif effort and effort not in model.efforts:
            side_blockers.append(
                f"effort={effort} is not declared for registry model {model_id}; "
                f"allowed: {', '.join(model.efforts)}"
            )

        credential_name = profile.credential_env_by_provider.get(provider)
        if credential_name and not env.get(credential_name):
            side_blockers.append(f"{credential_name} is not set")

        for reason in side_blockers:
            blockers.append(f"{side}: {reason}")
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
        "schema_version": 2,
        "host": profile.host,
        "experiment_id": experiment_id,
        "category": pair.get("category"),
        "kind": pair.get("kind"),
        "execution_target": target,
        "target_profile": profile.as_dict(),
        "target_ready": not target_blockers,
        "ready": not blockers,
        "sides": sides,
        "blockers": blockers,
        "evidence_boundary": (
            "Preflight proves target binding, registry compatibility, single-call semantics, "
            "and credential presence only. A real --apply run still requires the adapter to "
            "echo applied_configuration and a deterministic judge to establish benchmark outcome."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Direct Provider Experiment Preflight",
        "",
        f"Experiment: `{report['experiment_id']}`",
        f"Category: **{report.get('category') or 'unknown'}**",
        f"Kind: **{report.get('kind') or 'unknown'}**",
        f"Ready for direct provider adapter: **{'yes' if report['ready'] else 'no'}**",
        "",
        "## Sides",
        "",
    ]
    for side in ("A", "B"):
        entry = report["sides"][side]
        config = entry["configuration"]
        lines.append(
            f"- **{side}**: `{config['provider']} / {config['model_id']} / {config['effort']} / "
            f"{config['execution_mode']}` — **{'ready' if entry['ready'] else 'blocked'}**"
        )
        lines.extend(f"  - {reason}" for reason in entry["blockers"])
    if report["blockers"]:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {reason}" for reason in report["blockers"])
    lines.extend(["", "## Evidence boundary", "", report["evidence_boundary"], ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight one saved experiment against the built-in direct provider adapter."
    )
    parser.add_argument("--plan", required=True, help="JSON produced by experiment-plan")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 when the pair cannot be executed by the direct provider adapter",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_provider_experiment_preflight(_load_plan(args.plan), args.experiment_id)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    if args.require_ready and not report["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
