from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
    """Validate a saved pair for the built-in direct OpenAI/Anthropic adapter.

    The preflight performs no provider call. It proves only that the saved
    configuration is representable by the adapter and that the required
    credential is present in the current environment.
    """
    pair = find_experiment(plan, experiment_id)
    models = _model_lookup(registry or ModelRegistry())
    env = environ if environ is not None else os.environ
    target = target_summary(plan)

    target_blockers: list[str] = []
    if not target["bound"]:
        target_blockers.append("plan is not bound to an execution target")
    else:
        if target["host"] != "provider_api":
            target_blockers.append(
                f"plan is bound to host={target['host']!r}, not 'provider_api'"
            )
        if target["adapter"] != "provider_api":
            target_blockers.append(
                f"plan is bound to adapter={target['adapter']!r}, not 'provider_api'"
            )
        if target["adapter_contract_version"] != 1:
            target_blockers.append(
                "plan uses an unsupported provider-api adapter contract version "
                f"{target['adapter_contract_version']!r}"
            )

    blockers = [f"target: {reason}" for reason in target_blockers]
    sides: dict[str, dict[str, Any]] = {}

    for side in ("A", "B"):
        config = _side_configuration(pair, side)
        provider = str(config.get("provider") or "")
        model_id = str(config.get("model_id") or "")
        effort = str(config.get("effort") or "")
        execution_mode = str(config.get("execution_mode") or "")
        side_blockers: list[str] = []

        if provider not in {"openai", "anthropic"}:
            side_blockers.append(
                f"direct provider adapter supports openai/anthropic, not {provider or 'missing'}"
            )
        if execution_mode != "single":
            side_blockers.append(
                "direct provider adapter supports execution_mode=single, "
                f"not {execution_mode or 'missing'}"
            )

        model = models.get((provider, model_id))
        if model is None:
            side_blockers.append(
                f"registry does not contain provider/model {provider or 'missing'}/{model_id or 'missing'}"
            )
        elif effort not in model.efforts:
            side_blockers.append(
                f"effort={effort or 'missing'} is not declared for registry model {model_id}; "
                f"allowed: {', '.join(model.efforts)}"
            )

        credential_name = None
        if provider == "openai":
            credential_name = "OPENAI_API_KEY"
        elif provider == "anthropic":
            credential_name = "ANTHROPIC_API_KEY"
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
        "schema_version": 1,
        "host": "provider_api",
        "experiment_id": experiment_id,
        "category": pair.get("category"),
        "kind": pair.get("kind"),
        "execution_target": target,
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
