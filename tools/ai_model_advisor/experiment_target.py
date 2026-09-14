from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

_TARGETS: dict[str, dict[str, Any]] = {
    "hive": {
        "host": "hive",
        "adapter": "hive_litellm",
        "adapter_contract_version": 1,
        "preflight_module": "tools.ai_model_advisor.hive_experiment_preflight",
    },
    "provider_api": {
        "host": "provider_api",
        "adapter": "provider_api",
        "adapter_contract_version": 1,
        "preflight_module": "tools.ai_model_advisor.provider_experiment_preflight",
    },
}

_ADAPTER_MODULES: dict[str, str] = {
    "hive_litellm": "tools.ai_model_advisor.hive_litellm_adapter",
    "provider_api": "tools.ai_model_advisor.provider_api_adapter",
}
_ADAPTER_POLICIES: dict[str, dict[str, bool]] = {
    "hive_litellm": {"requires_external_judge": True},
    "provider_api": {"requires_external_judge": True},
}


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
    target = _TARGETS.get(host)
    if target is None:
        raise ExperimentTargetError(
            f"Unsupported execution target {host!r}; supported targets: {', '.join(sorted(_TARGETS))}"
        )

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


def target_requires_external_judge(plan: dict[str, Any]) -> bool:
    summary = target_summary(plan)
    if not summary["bound"]:
        return False
    policy = _ADAPTER_POLICIES.get(str(summary.get("adapter")), {})
    return bool(policy.get("requires_external_judge"))


def canonical_runner_for_target(
    plan: dict[str, Any],
    *,
    python_executable: str | None = None,
) -> list[str]:
    """Return canonical argv for a bound execution target.

    This is intentionally unavailable for unbound plans: custom/generic
    execution must remain explicit rather than guessing a host.
    """
    summary = target_summary(plan)
    if not summary["bound"]:
        raise ExperimentTargetError(
            "Cannot resolve a canonical runner for an unbound plan; bind an execution target first"
        )
    adapter = summary.get("adapter")
    module = _ADAPTER_MODULES.get(str(adapter))
    if module is None:
        raise ExperimentTargetError(
            f"Execution target adapter {adapter!r} has no registered runner module"
        )
    return [python_executable or sys.executable, "-m", module]


def _runner_module(argv: list[str]) -> str | None:
    for index, token in enumerate(argv[:-1]):
        if token == "-m":
            return argv[index + 1]
    for token in argv:
        normalized = token.replace("\\", "/")
        if normalized.endswith("hive_litellm_adapter.py"):
            return "tools.ai_model_advisor.hive_litellm_adapter"
        if normalized.endswith("provider_api_adapter.py"):
            return "tools.ai_model_advisor.provider_api_adapter"
    return None


def validate_runner_for_target(plan: dict[str, Any], runner_argv: list[str]) -> None:
    """Fail closed when a bound plan is executed through the wrong adapter.

    Unbound legacy/generic plans remain compatible. A bound plan must execute
    through the canonical adapter named by its execution-target contract.
    """
    summary = target_summary(plan)
    if not summary["bound"]:
        return
    adapter = summary.get("adapter")
    expected_module = _ADAPTER_MODULES.get(str(adapter))
    if expected_module is None:
        raise ExperimentTargetError(
            f"Execution target adapter {adapter!r} has no registered runner module"
        )
    actual_module = _runner_module(runner_argv)
    if actual_module != expected_module:
        raise ExperimentTargetError(
            "Bound experiment target requires runner module "
            f"{expected_module!r}, but argv resolves to {actual_module!r}. "
            "Rebind the plan deliberately instead of bypassing target provenance."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind a saved AI Model Advisor experiment plan to an explicit execution host."
    )
    parser.add_argument("--plan", required=True, help="JSON produced by experiment-plan")
    parser.add_argument("--host", required=True, choices=sorted(_TARGETS))
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
