from __future__ import annotations

from tools.ai_model_advisor.experiment_target import (
    ExperimentTargetError,
    bind_execution_target,
    validate_runner_for_target,
)
from tools.ai_model_advisor.provider_experiment_preflight import (
    evaluate_provider_experiment_preflight,
)


def _pair(
    *,
    provider_a: str = "openai",
    model_a: str = "gpt-5.6-terra",
    effort_a: str = "medium",
    provider_b: str = "anthropic",
    model_b: str = "claude-haiku-4-5-20251001",
    effort_b: str = "default",
) -> dict:
    return {
        "categories": [
            {
                "category": "implementation",
                "pairs": [
                    {
                        "experiment_id": "provider-target-01",
                        "category": "implementation",
                        "kind": "model",
                        "task_id_template": "implementation-provider-target-01-task-{nn}",
                        "primary": {
                            "provider": provider_a,
                            "model_id": model_a,
                            "effort": effort_a,
                            "execution_mode": "single",
                        },
                        "challenger": {
                            "provider": provider_b,
                            "model_id": model_b,
                            "effort": effort_b,
                            "execution_mode": "single",
                        },
                    }
                ],
            }
        ]
    }


def test_provider_api_target_binding_and_runner_provenance() -> None:
    plan = bind_execution_target(_pair(), "provider_api")

    validate_runner_for_target(
        plan,
        ["python", "-m", "tools.ai_model_advisor.provider_api_adapter"],
    )

    try:
        validate_runner_for_target(
            plan,
            ["python", "-m", "tools.ai_model_advisor.hive_litellm_adapter"],
        )
    except ExperimentTargetError as exc:
        assert "Bound experiment target requires runner module" in str(exc)
    else:
        raise AssertionError("wrong bound adapter should fail closed")


def test_unbound_plan_keeps_legacy_generic_runner_compatibility() -> None:
    validate_runner_for_target(_pair(), ["python", "./custom_adapter.py"])


def test_provider_preflight_accepts_openai_and_haiku_default_effort() -> None:
    plan = bind_execution_target(_pair(), "provider_api")
    report = evaluate_provider_experiment_preflight(
        plan,
        "provider-target-01",
        environ={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"},
    )

    assert report["ready"] is True
    assert report["target_ready"] is True
    assert report["sides"]["A"]["ready"] is True
    assert report["sides"]["B"]["ready"] is True
    assert report["sides"]["B"]["configuration"]["effort"] == "default"


def test_provider_preflight_blocks_missing_key_and_invalid_effort() -> None:
    plan = bind_execution_target(
        _pair(
            provider_b="anthropic",
            model_b="claude-haiku-4-5-20251001",
            effort_b="high",
        ),
        "provider_api",
    )
    report = evaluate_provider_experiment_preflight(
        plan,
        "provider-target-01",
        environ={"OPENAI_API_KEY": "x"},
    )

    assert report["ready"] is False
    assert any("effort=high" in blocker for blocker in report["sides"]["B"]["blockers"])
    assert "ANTHROPIC_API_KEY is not set" in report["sides"]["B"]["blockers"]


def test_provider_preflight_requires_explicit_provider_target_binding() -> None:
    report = evaluate_provider_experiment_preflight(
        _pair(),
        "provider-target-01",
        environ={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"},
    )

    assert report["ready"] is False
    assert report["target_ready"] is False
    assert any("not bound" in blocker for blocker in report["blockers"])
