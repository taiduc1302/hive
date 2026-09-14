from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

_TARGETS: dict[str, dict[str, Any]] = {
    "hive": {
        "host": "hive",
        "adapter": "hive_litellm",
        "adapter_contract_version": 1,
        "preflight_module": "tools.ai_model_advisor.hive_experiment_preflight",
    },
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
