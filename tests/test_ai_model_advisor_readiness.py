from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.readiness import (
    build_experiment_readiness,
    experiment_readiness_markdown,
)


def _record(
    model_id="gpt-5.6-terra",
    effort="medium",
    execution_mode="single",
    task_id=None,
    category="implementation",
    outcome="success",
    cost=0.10,
    latency=10.0,
):
    provider = "anthropic" if model_id.startswith("claude-") else "openai"
    return UsageRecord(
        provider=provider,
        model_id=model_id,
        effort=effort,
        execution_mode=execution_mode,
        outcome=outcome,
        task_category=category,
        task_id=task_id,
        cost_usd=cost,
        latency_seconds=latency,
    )


def test_readiness_explains_empty_history():
    report = build_experiment_readiness(FeedbackStore())
    assert report["records"] == 0
    assert report["evidence_ready_configs"] == 0
    assert report["rows"] == []
    assert "No empirical history" in experiment_readiness_markdown(report)


def test_readiness_counts_missing_exact_outcome_runs():
    report = build_experiment_readiness(FeedbackStore([_record(), _record()]))
    row = report["rows"][0]
    assert row["controlled_config"] is True
    assert row["quality_evidence"] == "below threshold"
    assert row["exact_quality_remaining"] == 1
    assert row["paired_efficiency_remaining"] == 3
    assert "Collect 1 more outcome run" in row["next_action"]


def test_historical_hive_rows_are_not_treated_as_controlled_config_evidence():
    records = [
        _record(effort="observed", execution_mode="hive_agent_loop")
        for _ in range(6)
    ]
    report = build_experiment_readiness(FeedbackStore(records))
    row = report["rows"][0]
    assert row["quality_evidence"] == "exact"
    assert row["controlled_config"] is False
    assert row["evidence_ready"] is False
    assert "explicit effort and execution mode" in row["next_action"]


def test_three_shared_successful_tasks_make_both_configs_efficiency_ready():
    records = []
    for index in range(3):
        task_id = f"endpoint-{index}"
        records.extend(
            [
                _record(task_id=task_id, cost=0.10, latency=10),
                _record(
                    model_id="claude-sonnet-5",
                    task_id=task_id,
                    cost=0.20,
                    latency=20,
                ),
            ]
        )

    report = build_experiment_readiness(FeedbackStore(records))
    assert report["controlled_configs"] == 2
    assert report["evidence_ready_configs"] == 2
    assert all(row["paired_efficiency_ready"] for row in report["rows"])
    assert all(row["paired_efficiency_remaining"] == 0 for row in report["rows"])
    assert all(row["exact_quality_remaining"] == 0 for row in report["rows"])
    assert all("benchmark anchor" in row["next_action"] for row in report["rows"])


def test_readiness_stays_scoped_by_task_category():
    records = [
        _record(task_id=f"impl-{index}", category="implementation")
        for index in range(3)
    ] + [
        _record(
            model_id="claude-sonnet-5",
            task_id=f"impl-{index}",
            category="research",
        )
        for index in range(3)
    ]
    report = build_experiment_readiness(FeedbackStore(records))
    assert {row["category"] for row in report["rows"]} == {"implementation", "research"}
    assert all(row["paired_comparable_tasks"] == 0 for row in report["rows"])
    assert all(row["paired_efficiency_remaining"] == 3 for row in report["rows"])
