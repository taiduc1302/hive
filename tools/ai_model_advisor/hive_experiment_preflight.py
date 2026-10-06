from __future__ import annotations

import argparse
import json
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
        raise ExperimentRunnerError(f"Experiment side {side} must be a configuration object")
    return config


def _hive_compatible_target_blockers(blockers: list[str]) -> list[str]:
    """Preserve legacy Hive-facing blocker wording across catalog refactors."""
    return [
        reason.replace(
            "plan uses unsupported adapter contract version",
            "plan uses an unsupported Hive adapter contract version",
        )
        for reason in blockers
    ]


def _catalog_configuration_blockers(
    configuration: dict[str, Any],
    registry: ModelRegistry,
) -> list[str]:
    provider = str(configuration.get("provider") or "")
    model_id = str(configuration.get("model_id") or "")
    effort = str(configuration.get("effort") or "")

    model = next(
        (candidate for candidate in registry.models if candidate.provider == provider and candidate.model_id == model_id),
        None,
    )
    if model is None:
        return [
            f"Advisor registry does not recognize {provider or 'missing'}/{model_id or 'missing'}; "
            "refresh the official model registry before spending benchmark credits"
        ]

    blockers: list[str] = []
    if model.status in {"retired", "deprecated"}:
        blockers.append(f"Advisor registry marks {provider}/{model_id} as {model.status}")
    if effort and effort != "default" and effort not in model.efforts:
        supported = ", ".join(model.efforts) or "provider default only"
        blockers.append(f"Advisor registry does not list effort={effort!r} for {provider}/{model_id}; supported explicit efforts: {supported}")
    return blockers


def evaluate_hive_experiment_preflight(
    plan: dict[str, Any],
    experiment_id: str,
    *,
    capability_report: dict[str, Any] | None = None,
    registry: ModelRegistry | None = None,
) -> dict[str, Any]:
    """Check whether a saved pair is executable by the current Hive Advisor adapter."""
    pair = find_experiment(plan, experiment_id)
    capabilities = capability_report or build_capability_report()
    catalog = registry or ModelRegistry()
    single_ready = bool(capabilities.get("transport", {}).get("single_call_evidence_ready"))
    profile = profile_for_host("hive")
    raw_target = plan.get("execution_target")
    target = target_summary(plan)

    target_blockers = _hive_compatible_target_blockers(
        target_binding_blockers(
            raw_target if isinstance(raw_target, dict) else None,
            profile,
            allow_unbound=True,
        )
    )
    blockers = [f"target: {reason}" for reason in target_blockers]

    sides: dict[str, dict[str, Any]] = {}
    for side in ("A", "B"):
        config = _side_configuration(pair, side)
        execution_mode = str(config.get("execution_mode") or "")
        provider = str(config.get("provider") or "")
        model_id = str(config.get("model_id") or "")
        effort = str(config.get("effort") or "")

        catalog_blockers = _catalog_configuration_blockers(config, catalog)
        side_blockers = configuration_blockers(config, profile)
        side_blockers.extend(catalog_blockers)
        if not single_ready:
            side_blockers.append("Hive single-call wire-evidence transport is not ready in this runtime")

        for reason in side_blockers:
            blockers.append(f"{side}: {reason}")
        sides[side] = {
            "configuration": {
                "provider": provider,
                "model_id": model_id,
                "effort": effort,
                "execution_mode": execution_mode,
            },
            "ready": not side_blockers,
            "catalog_ready": not catalog_blockers,
            "catalog_blockers": catalog_blockers,
            "blockers": side_blockers,
        }

    return {
        "schema_version": 3,
        "host": profile.host,
        "experiment_id": experiment_id,
        "category": pair.get("category"),
        "kind": pair.get("kind"),
        "execution_target": target,
        "target_profile": profile.as_dict(),
        "target_bound": bool(target["bound"]),
        "target_ready": not target_blockers,
        "target_blockers": target_blockers,
        "ready": not blockers,
        "sides": sides,
        "blockers": blockers,
        "runtime": capabilities,
        "catalog": {
            "as_of": catalog.as_of,
            "network_calls_performed": False,
            "runtime_support_proven": False,
        },
        "evidence_boundary": (
            "Preflight proves local host plumbing, execution-target compatibility, and "
            "catalog consistency only. Registry validation is not runtime/provider proof. "
            "A real --apply run must still prove the exact model and effort on Hive's "
            "post-transform request body before evidence is accepted."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    target = report.get("execution_target") or {}
    target_text = (
        f"{target.get('host')} / {target.get('adapter')} / contract-v{target.get('adapter_contract_version')}"
        if report.get("target_bound")
        else "unbound (legacy/generic plan)"
    )
    lines = [
        "# Hive Experiment Preflight",
        "",
        f"Experiment: `{report['experiment_id']}`",
        f"Category: **{report.get('category') or 'unknown'}**",
        f"Kind: **{report.get('kind') or 'unknown'}**",
        f"Execution target: **{target_text}**",
        f"Ready for Hive adapter: **{'yes' if report['ready'] else 'no'}**",
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
    parser = argparse.ArgumentParser(description="Preflight one saved experiment against the current Hive execution adapter.")
    parser.add_argument("--plan", required=True, help="JSON produced by experiment-plan")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 when the pair cannot be executed by the current Hive adapter",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_hive_experiment_preflight(_load_plan(args.plan), args.experiment_id)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    if args.require_ready and not report["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
