from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
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

    markdown = feedback_audit_markdown(audit)
    assert "Evidence by configuration" in markdown
    assert "gpt-5.6-terra" in markdown
