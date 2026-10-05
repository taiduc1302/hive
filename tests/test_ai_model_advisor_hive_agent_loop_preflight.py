from __future__ import annotations

from tools.ai_model_advisor.execution_targets import profile_for_host
from tools.ai_model_advisor.hive_agent_loop_experiment_preflight import (
    evaluate_hive_agent_loop_preflight,
)


def _plan() -> dict:
    config = {
        "provider": "openai",
        "model_id": "gpt-6-astra",
        "effort": "high",
        "execution_mode": "hive_agent_loop",
    }
    return {
        "execution_target": profile_for_host("hive_agent_loop").binding(),
        "categories": [
            {
                "category": "debugging",
                "pairs": [
                    {
                        "experiment_id": "exp-agent-loop",
                        "category": "debugging",
                        "kind": "execution_mode",
                        "task_id_template": "agent-loop-preflight-{nn}",
                        "primary": dict(config),
                        "challenger": dict(config),
                    }
                ],
            }
        ],
    }


def _capabilities(ready: bool = True) -> dict:
    return {
        "transport": {
            "single_call_evidence_ready": ready,
            "agent_loop_evidence_ready": ready,
        }
    }


def test_agent_loop_preflight_is_ready_with_runtime_and_credentials() -> None:
    report = evaluate_hive_agent_loop_preflight(
        _plan(),
        "exp-agent-loop",
        capability_report=_capabilities(),
        environ={"OPENAI_API_KEY": "placeholder"},
    )

    assert report["host"] == "hive_agent_loop"
    assert report["target_ready"] is True
    assert report["runtime_ready"] is True
    assert report["ready"] is True
    assert report["sides"]["A"]["ready"] is True
    assert report["sides"]["B"]["ready"] is True


def test_agent_loop_preflight_fails_closed_without_runtime_evidence() -> None:
    report = evaluate_hive_agent_loop_preflight(
        _plan(),
        "exp-agent-loop",
        capability_report=_capabilities(False),
        environ={"OPENAI_API_KEY": "placeholder"},
    )

    assert report["ready"] is False
    assert report["runtime_ready"] is False
    assert any("lifecycle + wire-evidence runtime" in item for item in report["blockers"])


def test_agent_loop_preflight_requires_provider_credential() -> None:
    report = evaluate_hive_agent_loop_preflight(
        _plan(),
        "exp-agent-loop",
        capability_report=_capabilities(),
        environ={},
    )

    assert report["ready"] is False
    assert "OPENAI_API_KEY is not set" in report["sides"]["A"]["blockers"]


def test_agent_loop_preflight_rejects_wrong_target_binding() -> None:
    plan = _plan()
    plan["execution_target"] = profile_for_host("hive").binding()

    report = evaluate_hive_agent_loop_preflight(
        plan,
        "exp-agent-loop",
        capability_report=_capabilities(),
        environ={"OPENAI_API_KEY": "placeholder"},
    )

    assert report["target_ready"] is False
    assert report["ready"] is False
    assert any("plan is bound to host='hive'" in item for item in report["blockers"])
