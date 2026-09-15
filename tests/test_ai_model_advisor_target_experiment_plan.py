from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.ai_model_advisor.execution_targets import configuration_blockers, profile_for_host
from tools.ai_model_advisor.experiment_target import target_summary
from tools.ai_model_advisor.models import WorkloadProfile
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.target_experiment_plan import build_target_experiment_plan

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _profiles() -> dict[str, WorkloadProfile]:
    return {
        "implementation": WorkloadProfile(
            coding=4.7,
            reasoning=4.4,
            agentic=4.8,
            ambiguity=4.0,
            breadth=5.0,
            parallelism=5.0,
            latency_sensitivity=2.0,
            cost_sensitivity=2.0,
            volume=3.0,
            categories={"implementation": 24},
            activity_count=24,
        )
    }


def _engine() -> RecommendationEngine:
    return RecommendationEngine(ModelRegistry(REGISTRY))


def _all_pair_configs(plan: dict[str, object]):
    for category in plan["categories"]:
        yield category["primary"]
        for pair in category["pairs"]:
            yield pair["primary"]
            yield pair["challenger"]


def test_target_plan_is_bound_at_creation() -> None:
    plan = build_target_experiment_plan(_profiles(), _engine(), "hive")

    assert target_summary(plan) == {
        "bound": True,
        "host": "hive",
        "adapter": "hive_litellm",
        "adapter_contract_version": 1,
    }
    assert plan["routing_scope"]["execution_target"] == "hive"


def test_every_planned_configuration_satisfies_target_contract() -> None:
    plan = build_target_experiment_plan(_profiles(), _engine(), "provider_api")
    profile = profile_for_host("provider_api")

    configs = list(_all_pair_configs(plan))
    assert configs
    for config in configs:
        assert configuration_blockers(config, profile) == []


def test_single_only_target_does_not_propose_execution_ab() -> None:
    plan = build_target_experiment_plan(_profiles(), _engine(), "hive")
    pairs = [pair for category in plan["categories"] for pair in category["pairs"]]

    assert pairs
    assert all(pair["kind"] != "execution" for pair in pairs)
    assert all(config["execution_mode"] == "single" for config in _all_pair_configs(plan))


def test_target_plan_keeps_model_and_effort_experiments_when_executable() -> None:
    plan = build_target_experiment_plan(
        _profiles(),
        _engine(),
        "provider_api",
        providers=["anthropic"],
    )
    pairs = [pair for category in plan["categories"] for pair in category["pairs"]]
    kinds = {pair["kind"] for pair in pairs}

    assert "model" in kinds
    assert "effort" in kinds
    assert "execution" not in kinds


def test_target_experiment_plan_cli_writes_bound_plan(tmp_path: Path) -> None:
    activity = tmp_path / "activity.json"
    activity.write_text(
        json.dumps(
            {
                "activities": [
                    {"text": "Implement repository feature and debug code"},
                    {"text": "Design agent architecture across many files"},
                    {"text": "Automate a broad parallel workflow"},
                ]
            }
        ),
        encoding="utf-8",
    )
    markdown = tmp_path / "plan.md"
    payload = tmp_path / "plan.json"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.ai_model_advisor.target_experiment_plan",
            "--input",
            str(activity),
            "--target",
            "provider_api",
            "--output",
            str(markdown),
            "--json-output",
            str(payload),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(payload.read_text(encoding="utf-8"))
    assert "Target-Aware AI Model Advisor Experiment Plan" in markdown.read_text(encoding="utf-8")
    assert data["execution_target"]["host"] == "provider_api"
    assert data["execution_target"]["adapter"] == "provider_api"
    assert data["experiments"] > 0
    assert all(
        config["execution_mode"] == "single"
        for config in _all_pair_configs(data)
    )
