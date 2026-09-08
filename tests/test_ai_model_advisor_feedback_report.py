from tools.ai_model_advisor.feedback import (
    CROSS_CONFIG_FEEDBACK_MIN,
    EXACT_FEEDBACK_MIN,
    PAIRED_EFFICIENCY_MIN,
    FeedbackStore,
    UsageRecord,
)
from tools.ai_model_advisor.feedback_report import (
    build_feedback_audit,
    feedback_audit_markdown,
)


def test_feedback_audit_explains_empty_history():
    audit = build_feedback_audit(FeedbackStore())
    assert audit["records"] == 0
    assert audit["rows"] == []
    markdown = feedback_audit_markdown(audit)
    assert "static registry" in markdown


def test_feedback_audit_thresholds_match_router_policy():
    audit = build_feedback_audit(FeedbackStore())
    assert audit["thresholds"] == {
        "exact_quality_observations": EXACT_FEEDBACK_MIN,
        "cross_config_observations": CROSS_CONFIG_FEEDBACK_MIN,
        "paired_efficiency_tasks": PAIRED_EFFICIENCY_MIN,
    }


def test_feedback_audit_reports_exact_and_cross_config_thresholds():
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="anthropic",
                model_id="claude-opus-5",
                effort="high",
                execution_mode="single",
                outcome="success",
                task_category="repo_review",
                source_id=f"source-{index}",
            )
            for index in range(6)
        ]
    )
    audit = build_feedback_audit(feedback)
    row = audit["rows"][0]

    assert row["observations"] == 6
    assert row["exact_quality_eligible"] is True
    assert row["cross_config_eligible"] is True
    assert row["quality_adjustment"] > 0
    assert row["unique_source_ids"] == 6


def test_feedback_audit_surfaces_paired_efficiency_evidence():
    records: list[UsageRecord] = []
    for index in range(3):
        task_id = f"impl-{index}"
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
                    input_tokens=1000,
                    output_tokens=200,
                    cached_tokens=500,
                    cache_creation_tokens=50,
                    credits=1.25,
                    task_category="implementation",
                    task_id=task_id,
                    source_id=f"terra-{index}",
                ),
                UsageRecord(
                    provider="anthropic",
                    model_id="claude-sonnet-5",
                    effort="medium",
                    execution_mode="single",
                    outcome="success",
                    latency_seconds=20,
                    cost_usd=0.20,
                    input_tokens=1200,
                    output_tokens=240,
                    cached_tokens=300,
                    cache_creation_tokens=80,
                    task_category="implementation",
                    task_id=task_id,
                    source_id=f"sonnet-{index}",
                ),
            ]
        )

    audit = build_feedback_audit(FeedbackStore(records))
    terra = next(row for row in audit["rows"] if row["model_id"] == "gpt-5.6-terra")
    sonnet = next(row for row in audit["rows"] if row["model_id"] == "claude-sonnet-5")

    assert terra["paired_comparable_tasks"] == 3
    assert terra["paired_efficiency_eligible"] is True
    assert terra["efficiency_adjustment"] > 0
    assert sonnet["efficiency_adjustment"] < 0
    assert terra["median_latency_seconds"] == 10.0
    assert terra["median_cost_usd"] == 0.1
    assert terra["median_input_tokens"] == 1000.0
    assert terra["median_output_tokens"] == 200.0
    assert terra["median_cache_read_ratio"] == 0.5
    assert terra["median_cache_creation_tokens"] == 50.0
    assert terra["median_credits"] == 1.25
    assert terra["credit_samples"] == 3
    assert sonnet["credit_samples"] == 0

    markdown = feedback_audit_markdown(audit)
    assert "Evidence by configuration" in markdown
    assert "gpt-5.6-terra" in markdown
    assert "median tokens in/out 1000/200" in markdown
    assert "median cache-read 50.0%" in markdown
    assert "median Hive credits 1.2500" in markdown
    assert "diagnostic-only" in markdown
