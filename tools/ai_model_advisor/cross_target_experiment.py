from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .execution_targets import (
    ExecutionTargetProfile,
    configuration_blockers,
    profile_for_host,
)
from .experiment_run import ExperimentRunnerError, find_experiment

_ALLOWED_HOSTS = ("hive_agent_loop", "hive_agent_loop_tool")


@dataclass(frozen=True)
class CrossTargetBinding:
    side_a_host: str
    side_b_host: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "side_targets": {
                "A": profile_for_host(self.side_a_host).binding(),
                "B": profile_for_host(self.side_b_host).binding(),
            },
        }


def _config(pair: dict[str, Any], side: str) -> dict[str, Any]:
    key = "primary" if side == "A" else "challenger"
    value = pair.get(key)
    if not isinstance(value, dict):
        raise ExperimentRunnerError(f"Experiment side {side} must be a configuration object")
    return value


def build_agent_loop_overhead_plan(
    plan: dict[str, Any],
    experiment_id: str,
    *,
    side_a_host: str = "hive_agent_loop",
    side_b_host: str = "hive_agent_loop_tool",
    source_side: str = "A",
) -> dict[str, Any]:
    """Derive a two-target execution-overhead benchmark from one saved pair.

    Model/provider/effort stay identical on both sides. Only the execution
    mode/host changes, so paired latency/cost evidence can be interpreted as
    execution overhead rather than model-quality drift.
    """
    if side_a_host not in _ALLOWED_HOSTS or side_b_host not in _ALLOWED_HOSTS:
        raise ExperimentRunnerError(
            "AgentLoop overhead plans only support hive_agent_loop and "
            "hive_agent_loop_tool targets"
        )
    if side_a_host == side_b_host:
        raise ExperimentRunnerError("Cross-target overhead plan requires two different hosts")

    pair = find_experiment(plan, experiment_id)
    if source_side not in {"A", "B"}:
        raise ExperimentRunnerError("source_side must be A or B")
    source = copy.deepcopy(_config(pair, source_side))
    for key in ("provider", "model_id", "effort"):
        if not source.get(key):
            raise ExperimentRunnerError(
                f"Cross-target source side {source_side} is missing {key!r}"
            )

    derived = copy.deepcopy(plan)
    derived_pair = find_experiment(derived, experiment_id)
    derived_pair["primary"] = copy.deepcopy(source)
    derived_pair["challenger"] = copy.deepcopy(source)
    profiles = {
        "A": profile_for_host(side_a_host),
        "B": profile_for_host(side_b_host),
    }
    for side, key in (("A", "primary"), ("B", "challenger")):
        config = derived_pair[key]
        profile = profiles[side]
        config["execution_mode"] = profile.execution_modes[0]
        blockers = configuration_blockers(config, profile)
        if blockers:
            raise ExperimentRunnerError(
                f"Cross-target side {side} is incompatible with {profile.host}: "
                + "; ".join(blockers)
            )

    derived.pop("execution_target", None)
    derived["cross_target_execution"] = CrossTargetBinding(
        side_a_host=side_a_host,
        side_b_host=side_b_host,
    ).as_dict()
    derived_pair["kind"] = "execution_target_overhead"
    derived_pair["source_side"] = source_side
    return derived


def cross_target_profiles(
    plan: dict[str, Any],
) -> dict[str, ExecutionTargetProfile]:
    block = plan.get("cross_target_execution")
    if not isinstance(block, dict) or block.get("schema_version") != 1:
        raise ExperimentRunnerError("Plan is not bound to a cross-target execution contract")
    raw_targets = block.get("side_targets")
    if not isinstance(raw_targets, dict):
        raise ExperimentRunnerError("Cross-target plan is missing side_targets")

    profiles: dict[str, ExecutionTargetProfile] = {}
    for side in ("A", "B"):
        target = raw_targets.get(side)
        if not isinstance(target, dict):
            raise ExperimentRunnerError(f"Cross-target plan is missing side {side} target")
        host = str(target.get("host") or "")
        profile = profile_for_host(host)
        if target != profile.binding():
            raise ExperimentRunnerError(
                f"Cross-target side {side} binding does not match current target contract"
            )
        profiles[side] = profile

    if profiles["A"].host == profiles["B"].host:
        raise ExperimentRunnerError("Cross-target plan must bind A and B to different hosts")
    return profiles
