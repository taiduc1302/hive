from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def test_registry_loads_current_models_and_excludes_limited_by_default():
    registry = ModelRegistry(REGISTRY)
    ids = {model.model_id for model in registry.candidates()}
    assert "gpt-5.6-sol" in ids
    assert "claude-sonnet-5" in ids
    assert "claude-opus-5" in ids
    assert "claude-fable-5-1" in ids
    assert "gpt-6-astra" not in ids
    assert "gpt-6-astra" in {model.model_id for model in registry.candidates(include_limited=True)}


def test_simple_high_volume_work_prefers_efficient_model():
    analyzer = ActivityAnalyzer()
    workload = analyzer.from_texts(["Implement a small endpoint and update one test", "Write a simple script for a routine automation"])
    workload.cost_sensitivity = 5
    workload.latency_sensitivity = 5
    recs = RecommendationEngine(ModelRegistry(REGISTRY)).recommend(workload, top_n=3)
    assert recs
    assert recs[0].model_id != "claude-fable-5-1"
    assert recs[0].effort in {"none", "low", "medium", "high"}


def test_large_anthropic_repo_audit_uses_orchestration_mode():
    analyzer = ActivityAnalyzer()
    workload = analyzer.from_texts([
        "Audit the whole repository architecture across hundreds of files",
        "Compare many modules in parallel, investigate unknown root causes, and verify every result",
        "Run autonomously overnight with many steps and a final code review",
        "Research all integrations and migration paths for the entire system",
    ])
    recs = RecommendationEngine(ModelRegistry(REGISTRY)).recommend(workload, providers=["anthropic"], top_n=3)
    assert recs
    assert recs[0].execution_mode in {"ultracode", "dynamic_workflow"}
    assert recs[0].effort in {"high", "xhigh", "max"}


def test_chatgpt_export_only_uses_user_messages(tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text('[{"title":"Repo audit","mapping":{"1":{"message":{"author":{"role":"user"},"content":{"parts":["Debug the whole repo"]}}},"2":{"message":{"author":{"role":"assistant"},"content":{"parts":["irrelevant assistant text"]}}}}}]', encoding="utf-8")
    profile = ActivityAnalyzer().from_chatgpt_export(export)
    assert profile.activity_count == 2
    assert profile.coding > 1
    assert all("irrelevant assistant text" not in item for item in profile.evidence)
