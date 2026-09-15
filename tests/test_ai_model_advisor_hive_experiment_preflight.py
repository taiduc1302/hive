from __future__ import annotations

from tools.ai_model_advisor.experiment_target import bind_execution_target
from tools.ai_model_advisor.hive_experiment_preflight import (
    evaluate_hive_experiment_preflight,
    render_markdown,
)


def _plan(*, execution_mode: str = "single", provider: str = "openai") -> dict:
    return {
        "categories": [
            {
                "pairs": [
                    {
                        "experiment_id": "coding-model-01",
                        "category": "implementation",
                        "kind": "model",
                        "task_id_template": "implementation-model-{nn}",
                        "primary": {
                            "provider": provider,
                            "model_id": "gpt-5.6-sol",
                            "effort": "medium",
                            "execution_mode": execution_mode,
                        },
                        "challenger": {
                            "provider": "anthropic",
                            "model_id": "claude-opus-5",
                            "effort": "high",
                            "execution_mode": execution_mode,
                        },
                    }
                ]
            }
        ]
    }


def _capabilities(*, ready: bool = True) -> dict:
    return {
        "transport": {"single_call_evidence_ready": ready},
        "controls": {
            "execution_modes": {"single": "supported_by_advisor_adapter"},
        },
    }


def test_hive_experiment_preflight_accepts_supported_single_pair() -> None:
    report = evaluate_hive_experiment_preflight(
        _plan(),
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is True
    assert report["target_bound"] is False
    assert report["sides"]["A"]["ready"] is True
    assert report["sides"]["B"]["ready"] is True
    assert report["blockers"] == []


def test_hive_experiment_preflight_accepts_explicit_hive_target() -> None:
    report = evaluate_hive_experiment_preflight(
        bind_execution_target(_plan(), "hive"),
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is True
    assert report["target_bound"] is True
    assert report["target_ready"] is True
    assert report["execution_target"]["host"] == "hive"
    assert report["execution_target"]["adapter"] == "hive_litellm"


def test_hive_experiment_preflight_blocks_wrong_bound_host() -> None:
    plan = _plan()
    plan["execution_target"] = {
        "host": "other",
        "adapter": "other",
        "adapter_contract_version": 1,
    }

    report = evaluate_hive_experiment_preflight(
        plan,
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is False
    assert report["target_ready"] is False
    assert any("not 'hive'" in blocker for blocker in report["blockers"])


def test_hive_experiment_preflight_blocks_wrong_adapter_contract_version() -> None:
    plan = bind_execution_target(_plan(), "hive")
    plan["execution_target"]["adapter_contract_version"] = 2

    report = evaluate_hive_experiment_preflight(
        plan,
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is False
    assert any("unsupported Hive adapter contract version" in blocker for blocker in report["blockers"])


def test_hive_experiment_preflight_blocks_unimplemented_execution_mode() -> None:
    report = evaluate_hive_experiment_preflight(
        _plan(execution_mode="subagents"),
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is False
    assert any("execution_mode=single" in blocker for blocker in report["blockers"])


def test_hive_experiment_preflight_blocks_unsupported_provider() -> None:
    report = evaluate_hive_experiment_preflight(
        _plan(provider="gemini"),
        "coding-model-01",
        capability_report=_capabilities(),
    )

    assert report["ready"] is False
    assert any("supports openai/anthropic" in blocker for blocker in report["blockers"])


def test_hive_experiment_preflight_blocks_when_wire_transport_not_ready() -> None:
    report = evaluate_hive_experiment_preflight(
        _plan(),
        "coding-model-01",
        capability_report=_capabilities(ready=False),
    )

    assert report["ready"] is False
    assert any("wire-evidence transport is not ready" in blocker for blocker in report["blockers"])


def test_hive_experiment_preflight_markdown_shows_blocked_side() -> None:
    report = evaluate_hive_experiment_preflight(
        _plan(execution_mode="dynamic_workflow"),
        "coding-model-01",
        capability_report=_capabilities(),
    )

    markdown = render_markdown(report)
    assert "Hive Experiment Preflight" in markdown
    assert "Execution target: **unbound (legacy/generic plan)**" in markdown
    assert "Ready for Hive adapter: **no**" in markdown
    assert "dynamic_workflow" in markdown
    assert "post-transform request body" in markdown
