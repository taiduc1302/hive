import json
from pathlib import Path

from tools.ai_model_advisor import sources as source_module
from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.matrix import build_routing_matrix, routing_matrix_markdown
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.sources import (
    ScanReport,
    SourceResult,
    baseline_from_report,
    scan_official_sources,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def test_registry_loads_current_models():
    registry = ModelRegistry(REGISTRY)
    ids = {model.model_id for model in registry.candidates()}
    assert "gpt-6-astra" in ids
    assert "gpt-5.6-sol" in ids
    assert "claude-sonnet-5" in ids
    assert "claude-opus-5" in ids
    assert "claude-fable-5-1" in ids


def test_simple_high_volume_work_prefers_efficient_model():
    analyzer = ActivityAnalyzer()
    workload = analyzer.from_texts(
        [
            "Implement a small endpoint and update one test",
            "Write a simple script for a routine automation",
        ]
    )
    workload.cost_sensitivity = 5
    workload.latency_sensitivity = 5
    recs = RecommendationEngine(ModelRegistry(REGISTRY)).recommend(workload, top_n=3)
    assert recs
    assert recs[0].model_id not in {"claude-fable-5-1", "gpt-6-astra"}
    assert recs[0].effort in {"none", "low", "medium", "high"}


def test_large_anthropic_repo_audit_uses_orchestration_mode():
    analyzer = ActivityAnalyzer()
    workload = analyzer.from_texts(
        [
            "Audit the whole repository architecture across hundreds of files",
            "Compare many modules in parallel, investigate unknown root causes, and verify every result",
            "Run autonomously overnight with many steps and a final code review",
            "Research all integrations and migration paths for the entire system",
        ]
    )
    recs = RecommendationEngine(ModelRegistry(REGISTRY)).recommend(
        workload,
        providers=["anthropic"],
        top_n=3,
    )
    assert recs
    assert recs[0].execution_mode in {"ultracode", "dynamic_workflow"}
    assert recs[0].effort in {"high", "xhigh", "max"}


def test_chatgpt_export_only_uses_user_messages(tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text(
        '[{"title":"Repo audit","mapping":{"1":{"message":{"author":{"role":"user"},"content":{"parts":["Debug the whole repo"]}}},"2":{"message":{"author":{"role":"assistant"},"content":{"parts":["irrelevant assistant text"]}}}}}]',
        encoding="utf-8",
    )
    profile = ActivityAnalyzer().from_chatgpt_export(export)
    assert profile.activity_count == 2
    assert profile.coding > 1
    assert all("irrelevant assistant text" not in item for item in profile.evidence)


def test_feedback_requires_repeated_evidence_before_adjusting():
    two = FeedbackStore(
        [
            UsageRecord("anthropic", "claude-sonnet-5", "high", "single", "failure"),
            UsageRecord("anthropic", "claude-sonnet-5", "high", "single", "failure"),
        ]
    )
    assert two.adjustment("claude-sonnet-5", "high", "single") == 0

    four = FeedbackStore(
        [
            UsageRecord("anthropic", "claude-sonnet-5", "high", "single", "success")
            for _ in range(4)
        ]
    )
    assert four.adjustment("claude-sonnet-5", "high", "single") > 0


def test_feedback_is_scoped_to_task_category():
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="anthropic",
                model_id="claude-opus-5",
                effort="high",
                execution_mode="single",
                outcome="success",
                task_category="repo_review",
            )
            for _ in range(4)
        ]
    )

    assert feedback.adjustment(
        "claude-opus-5",
        "high",
        "single",
        "repo_review",
    ) > 0
    assert (
        feedback.adjustment(
            "claude-opus-5",
            "high",
            "single",
            "implementation",
        )
        == 0
    )


def test_category_feedback_falls_back_to_legacy_untagged_records():
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="openai",
                model_id="gpt-5.6-terra",
                effort="medium",
                execution_mode="single",
                outcome="success",
            )
            for _ in range(4)
        ]
    )

    assert feedback.adjustment(
        "gpt-5.6-terra",
        "medium",
        "single",
        "implementation",
    ) > 0


def test_feedback_jsonl_round_trip(tmp_path):
    path = tmp_path / "feedback.jsonl"
    record = UsageRecord(
        provider="openai",
        model_id="gpt-5.6-terra",
        effort="medium",
        execution_mode="single",
        outcome="partial",
        retries=1,
        cost_usd=0.42,
        task_id="estimate-42",
    )
    FeedbackStore.append(path, record)
    loaded = FeedbackStore.load(path)
    assert loaded.records == (record,)


def test_paired_efficiency_rewards_lower_cost_and_latency():
    records: list[UsageRecord] = []
    for task_id in ("impl-1", "impl-2", "impl-3", "impl-4"):
        records.extend(
            [
                UsageRecord(
                    provider="openai",
                    model_id="gpt-5.6-terra",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=10,
                    cost_usd=0.10,
                    task_category="implementation",
                    task_id=task_id,
                ),
                UsageRecord(
                    provider="anthropic",
                    model_id="claude-sonnet-5",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=20,
                    cost_usd=0.20,
                    task_category="implementation",
                    task_id=task_id,
                ),
            ]
        )

    feedback = FeedbackStore(records)
    terra = feedback.efficiency_adjustment(
        "gpt-5.6-terra",
        "medium",
        "single",
        "implementation",
        latency_sensitivity=5,
        cost_sensitivity=5,
    )
    sonnet = feedback.efficiency_adjustment(
        "claude-sonnet-5",
        "medium",
        "single",
        "implementation",
        latency_sensitivity=5,
        cost_sensitivity=5,
    )
    assert terra > 0
    assert sonnet < 0


def test_efficiency_ignores_unpaired_absolute_metrics():
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="openai",
                model_id="gpt-5.6-terra",
                effort="medium",
                execution_mode="single",
                outcome="success",
                latency_seconds=3,
                cost_usd=0.01,
                task_category="implementation",
                task_id=f"unpaired-{index}",
            )
            for index in range(4)
        ]
    )
    assert (
        feedback.efficiency_adjustment(
            "gpt-5.6-terra",
            "medium",
            "single",
            "implementation",
        )
        == 0
    )


def test_efficiency_does_not_cross_task_categories():
    records: list[UsageRecord] = []
    for task_id in ("shared-1", "shared-2", "shared-3"):
        records.extend(
            [
                UsageRecord(
                    provider="openai",
                    model_id="gpt-5.6-terra",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=5,
                    cost_usd=0.05,
                    task_category="implementation",
                    task_id=task_id,
                ),
                UsageRecord(
                    provider="anthropic",
                    model_id="claude-sonnet-5",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=15,
                    cost_usd=0.15,
                    task_category="research",
                    task_id=task_id,
                ),
            ]
        )
    feedback = FeedbackStore(records)
    assert (
        feedback.efficiency_adjustment(
            "gpt-5.6-terra",
            "medium",
            "single",
            "implementation",
        )
        == 0
    )


def test_failed_fast_attempt_is_not_rewarded_for_efficiency():
    records: list[UsageRecord] = []
    for task_id in ("failure-1", "failure-2", "failure-3"):
        records.extend(
            [
                UsageRecord(
                    provider="openai",
                    model_id="gpt-5.6-terra",
                    effort="medium",
                    execution_mode="single",
                    outcome="failure",
                    latency_seconds=1,
                    cost_usd=0.01,
                    task_category="implementation",
                    task_id=task_id,
                ),
                UsageRecord(
                    provider="anthropic",
                    model_id="claude-sonnet-5",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=20,
                    cost_usd=0.20,
                    task_category="implementation",
                    task_id=task_id,
                ),
            ]
        )
    feedback = FeedbackStore(records)
    assert (
        feedback.efficiency_adjustment(
            "gpt-5.6-terra",
            "medium",
            "single",
            "implementation",
        )
        == 0
    )


def test_source_scan_detects_signal_change_against_saved_baseline(tmp_path, monkeypatch):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "as_of": "2026-09-06",
                "official_sources": [
                    {"id": "test_source", "url": "https://example.test/models"}
                ],
                "models": [
                    {
                        "provider": "anthropic",
                        "model_id": "claude-sonnet-5",
                        "label": "Claude Sonnet 5",
                        "source_ids": ["test_source"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = ModelRegistry(registry_path)

    monkeypatch.setattr(source_module, "_fetch_text", lambda _url: "Claude Sonnet 5")
    first = scan_official_sources(registry)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline_from_report(first)), encoding="utf-8")

    monkeypatch.setattr(source_module, "_fetch_text", lambda _url: "Claude Opus 5")
    second = scan_official_sources(registry, baseline_path)
    assert second.changed_sources == ["test_source"]


def test_failed_source_preserves_previous_baseline_hash():
    report = ScanReport(
        generated_at="2026-09-06T00:00:00+00:00",
        registry_as_of="2026-09-06",
        changed_sources=[],
        unknown_signals=[],
        results=[
            SourceResult(
                source_id="test_source",
                url="https://example.test/models",
                ok=False,
                signal_hash="",
                signals=[],
                error="temporary network failure",
            )
        ],
    )
    baseline = baseline_from_report(
        report,
        {"source_hashes": {"test_source": "previous-hash"}},
    )
    assert baseline["source_hashes"]["test_source"] == "previous-hash"


def test_category_profiles_keep_routing_context_separate():
    analyzer = ActivityAnalyzer()
    profiles = analyzer.category_profiles_from_texts(
        [
            "Implement a feature in the API",
            "Debug an unknown root cause in the whole repository",
            "Research and compare the latest agent orchestration options",
        ]
    )
    assert {"implementation", "debugging", "repo_review", "research"} <= set(profiles)
    for category, profile in profiles.items():
        assert profile.categories == {category: profile.activity_count}


def test_routing_matrix_returns_actionable_rows():
    analyzer = ActivityAnalyzer()
    profiles = analyzer.category_profiles_from_texts(
        [
            "Implement a small endpoint",
            "Implement another backend feature",
            "Audit the whole repository architecture and investigate unknown bugs",
        ]
    )
    registry = ModelRegistry(REGISTRY)
    rows = build_routing_matrix(profiles, RecommendationEngine(registry))
    assert rows
    assert all(row["primary"]["model_id"] for row in rows)
    assert all(row["primary"]["effort"] for row in rows)
    markdown = routing_matrix_markdown(rows, registry.as_of)
    assert "AI Model Routing Matrix" in markdown
    assert "implementation" in markdown
