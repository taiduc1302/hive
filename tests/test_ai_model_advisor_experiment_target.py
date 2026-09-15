from __future__ import annotations

import pytest

from tools.ai_model_advisor.experiment_target import (
    ExperimentTargetError,
    bind_execution_target,
    target_summary,
)


def _plan() -> dict:
    return {"categories": [], "experiments": 0}


def test_bind_execution_target_adds_hive_contract_without_mutating_input() -> None:
    plan = _plan()

    bound = bind_execution_target(plan, "hive")

    assert "execution_target" not in plan
    assert bound["execution_target"] == {
        "host": "hive",
        "adapter": "hive_litellm",
        "adapter_contract_version": 1,
        "preflight_module": "tools.ai_model_advisor.hive_experiment_preflight",
    }
    assert target_summary(bound) == {
        "bound": True,
        "host": "hive",
        "adapter": "hive_litellm",
        "adapter_contract_version": 1,
    }


def test_bind_execution_target_is_idempotent_for_same_contract() -> None:
    first = bind_execution_target(_plan(), "hive")
    second = bind_execution_target(first, "hive")

    assert second == first
    assert second is not first


def test_bind_execution_target_rejects_accidental_rebind() -> None:
    plan = _plan()
    plan["execution_target"] = {
        "host": "other",
        "adapter": "other",
        "adapter_contract_version": 9,
    }

    with pytest.raises(ExperimentTargetError, match="different execution_target"):
        bind_execution_target(plan, "hive")


def test_bind_execution_target_allows_explicit_replace() -> None:
    plan = _plan()
    plan["execution_target"] = {"host": "other"}

    rebound = bind_execution_target(plan, "hive", replace=True)

    assert rebound["execution_target"]["host"] == "hive"


def test_bind_execution_target_rejects_unknown_host() -> None:
    with pytest.raises(ExperimentTargetError, match="Unsupported execution target"):
        bind_execution_target(_plan(), "chatgpt_work")


def test_target_summary_reports_unbound_plan() -> None:
    assert target_summary(_plan()) == {
        "bound": False,
        "host": None,
        "adapter": None,
        "adapter_contract_version": None,
    }
