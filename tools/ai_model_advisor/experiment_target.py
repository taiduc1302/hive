from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from .execution_targets import (
    ExecutionTargetCatalogError,
    profile_for_adapter,
    profile_for_host,
    target_catalog,
)


class ExperimentTargetError(ValueError):
    """Raised when an experiment plan cannot be safely bound to a target host."""


def _load_plan(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExperimentTargetError("Experiment plan root must be a JSON object")
    return data


def bind_execution_target(
    plan: dict[str, Any],
    host: str,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Return a copy of *plan* with an explicit execution-target contract."""
    try:
        target = profile_for_host(host).binding()
    except ExecutionTargetCatalogError as exc:
        raise ExperimentTargetError(str(exc)) from exc

    existing = plan.get("execution_target")
    if existing is not None and not replace:
        if existing == target:
            return copy.deepcopy(plan)
        raise ExperimentTargetError(
            "Experiment plan already has a different execution_target; use --replace only for a deliberate rebind"
        )

    bound = copy.deepcopy(plan)
    bound["execution_target"] = copy.deepcopy(target)
    return bound


def target_summary(plan: dict[str, Any]) -> dict[str, Any]:
    target = plan.get("execution_target")
    if not isinstance(target, dict):
        return {
            "bound": False,
            "host": None,
            "adapter": None,
            "adapter_contract_version": None,
        }
    return {
        "bound": True,
        "host": target.get("host"),
        "adapter": target.get("adapter"),
        "adapter_contract_version": target.get("adapter_contract_version"),
    }


def _profile_from_bound_plan(plan: dict[str, Any]):
    summary = target_summary(plan)
    if not summary["bound"]:
        raise ExperimentTargetError(
            "Cannot resolve execution-target metadata for an unbound plan"
        )
    try:
        return profile_for_adapter(str(summary.get("adapter")))
    except ExecutionTargetCatalogError as exc:
        raise ExperimentTargetError(str(exc)) from exc


def target_requires_external_judge(plan: dict[str, Any]) -> bool:
    if not target_summary(plan)["bound"]:
        return False
    return _profile_from_bound_plan(plan).requires_external_judge


def canonical_runner_for_target(
    plan: dict[str, Any],
    *,
    python_executable: str | None = None,
) -> list[str]:
    """Return canonical argv for a bound execution target."""
    if not target_summary(plan)["bound"]:
        raise ExperimentTargetError(
            "Cannot resolve a canonical runner for an unbound plan; bind an execution target first"
        )
    profile = _profile_from_bound_plan(plan)
    return [python_executable or sys.executable, "-m", profile.runner_module]


def _runner_module(argv: list[str]) -> str | None:
    for index, token in enumerate(argv[:-1]):
        if token == "-m":
            return argv[index + 1]
    known_modules = {
        profile["runner_module"] for profile in target_catalog().values()
    }
    for token in argv:
        normalized = token.replace("\\", "/")
        for module in known_modules:
            filename = module.rsplit(".", 1)[-1] + ".py"
            if normalized.endswith(filename):
                return module
    return None


def validate_runner_for_target(plan: dict[str, Any], runner_argv: list[str]) -> None:
    """Fail closed when a bound plan is executed through the wrong adapter."""
    if not target_summary(plan)["bound"]:
        return
    profile = _profile_from_bound_plan(plan)
    actual_module = _runner_module(runner_argv)
    if actual_module != profile.runner_module:
        raise ExperimentTargetError(
            "Bound experiment target requires runner module "
            f"{profile.runner_module!r}, but argv resolves to {actual_module!r}. "
            "Rebind the plan deliberately instead of bypassing target provenance."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind a saved AI Model Advisor experiment plan to an explicit execution host."
    )
    parser.add_argument("--plan", required=True, help="JSON produced by experiment-plan")
    parser.add_argument("--host", required=True, choices=sorted(target_catalog()))
    parser.add_argument("--output", required=True, help="Path for the bound JSON plan")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Deliberately replace a different existing execution_target",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bound = bind_execution_target(_load_plan(args.plan), args.host, replace=args.replace)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bound, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
