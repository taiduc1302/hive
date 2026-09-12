from __future__ import annotations

import json
from pathlib import Path

from tools.ai_model_advisor import sources as source_module
from tools.ai_model_advisor.models import WorkloadProfile
from tools.ai_model_advisor.provider_api_adapter import build_provider_request
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.sources import scan_official_sources

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _anthropic_payload(effort: str = "default") -> dict[str, object]:
    return {
        "schema_version": 1,
        "acceptance_mode": "external_judge",
        "task": "Return the fixed benchmark result.",
        "configuration": {
            "provider": "anthropic",
            "model_id": "claude-haiku-4-5-20251001",
            "effort": effort,
            "execution_mode": "single",
        },
    }


def test_registry_includes_current_haiku_fast_tier():
    registry = ModelRegistry(REGISTRY)
    haiku = next(model for model in registry.models if model.label == "Claude Haiku 4.5")

    assert haiku.model_id == "claude-haiku-4-5-20251001"
    assert haiku.context_tokens == 200_000
    assert haiku.max_output_tokens == 64_000
    assert haiku.input_usd_per_mtok == 1.0
    assert haiku.output_usd_per_mtok == 5.0
    assert haiku.efforts == ("default",)
    assert haiku.default_effort == "default"
    assert haiku.execution_modes == ("single",)


def test_source_scan_recognizes_haiku_alias_and_snapshot(tmp_path, monkeypatch):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "as_of": "2026-09-12",
                "official_sources": [
                    {"id": "anthropic", "url": "https://example.test/models"}
                ],
                "models": [
                    {
                        "provider": "anthropic",
                        "model_id": "claude-haiku-4-5-20251001",
                        "label": "Claude Haiku 4.5",
                        "source_ids": ["anthropic"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = ModelRegistry(registry_path)
    monkeypatch.setattr(
        source_module,
        "_fetch_text",
        lambda _url: (
            "Claude Haiku 4.5 claude-haiku-4-5 "
            "claude-haiku-4-5-20251001"
        ),
    )

    report = scan_official_sources(registry)

    assert report.unknown_signals == []
    assert "claude-haiku-4-5-20251001" in report.results[0].signals


def test_anthropic_provider_default_effort_omits_output_config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-secret")

    request = build_provider_request(_anthropic_payload())

    assert request.body["model"] == "claude-haiku-4-5-20251001"
    assert "output_config" not in request.body


def test_anthropic_explicit_effort_still_uses_output_config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-secret")

    request = build_provider_request(_anthropic_payload("medium"))

    assert request.body["output_config"] == {"effort": "medium"}


def test_cost_sensitive_anthropic_routine_can_choose_haiku():
    workload = WorkloadProfile(
        coding=1.0,
        reasoning=1.0,
        agentic=1.0,
        ambiguity=1.0,
        breadth=1.0,
        parallelism=1.0,
        latency_sensitivity=5.0,
        cost_sensitivity=5.0,
        volume=5.0,
        activity_count=20,
    )

    recommendations = RecommendationEngine(ModelRegistry(REGISTRY)).recommend(
        workload,
        providers=["anthropic"],
        top_n=4,
    )

    assert recommendations[0].model_id == "claude-haiku-4-5-20251001"
    assert recommendations[0].effort == "default"
    assert recommendations[0].execution_mode == "single"
