from tools.ai_model_advisor.experiment_evaluate import evaluate_experiment_plan
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord


def _config(model_id, effort="medium", execution="single"):
    return {
        "provider": "anthropic" if model_id.startswith("claude-") else "openai",
        "model_id": model_id,
        "effort": effort,
        "execution_mode": execution,
    }


def _plan():
    return {
        "categories": [
            {
                "category": "implementation",
                "pairs": [
                    {
                        "experiment_id": "abc123def456",
                        "kind": "model",
                        "category": "implementation",
                        "primary": _config("gpt-5.6-terra"),
                        "challenger": _config("claude-sonnet-5"),
                        "task_id_template": "implementation-abc123def456-task-{nn}",
                    }
                ],
            }
        ]
    }


def _record(
    model_id,
    task_id,
    outcome="success",
    retries=0,
    cost=0.10,
    latency=10.0,
):
    return UsageRecord(
        provider="anthropic" if model_id.startswith("claude-") else "openai",
        model_id=model_id,
        effort="medium",
        execution_mode="single",
        outcome=outcome,
        retries=retries,
        cost_usd=cost,
        latency_seconds=latency,
        task_category="implementation",
        task_id=task_id,
    )


def _paired_records(a_outcomes, b_outcomes, *, a_cost=0.10, b_cost=0.20):
    records = []
    for index, (a_outcome, b_outcome) in enumerate(zip(a_outcomes, b_outcomes, strict=True), start=1):
        task_id = f"implementation-abc123def456-task-{index:02d}"
        records.extend(
            [
                _record(
                    "gpt-5.6-terra",
                    task_id,
                    outcome=a_outcome,
                    cost=a_cost,
                    latency=10,
                ),
                _record(
                    "claude-sonnet-5",
                    task_id,
                    outcome=b_outcome,
                    cost=b_cost,
                    latency=20,
                ),
            ]
        )
    return records


def test_evaluation_requires_three_complete_paired_tasks():
    report = evaluate_experiment_plan(
        _plan(),
        FeedbackStore(_paired_records(["success", "success"], ["success", "success"])),
    )
    result = report["evaluations"][0]
    assert result["paired_tasks"] == 2
    assert result["conclusion"] == "insufficient_evidence"
    assert result["suggested_winner"] is None
    assert report["evidence_ready"] == 0


def test_quality_lead_takes_precedence_over_cost_and_latency():
    records = _paired_records(
        ["success", "success", "success"],
        ["failure", "success", "partial"],
        a_cost=0.30,
        b_cost=0.05,
    )
    report = evaluate_experiment_plan(_plan(), FeedbackStore(records))
    result = report["evaluations"][0]

    assert result["paired_tasks"] == 3
    assert result["quality_leader"] == "A"
    assert result["cost_leader"] == "B"
    assert result["latency_leader"] == "A"
    assert result["conclusion"] == "quality_lead"
    assert result["suggested_winner"] == "A"
    assert report["evidence_ready"] == 1


def test_tied_quality_can_use_consistent_efficiency_lead():
    records = []
    for index in range(1, 4):
        task_id = f"implementation-abc123def456-task-{index:02d}"
        records.extend(
            [
                _record(
                    "gpt-5.6-terra",
                    task_id,
                    outcome="success",
                    retries=0,
                    cost=0.10,
                    latency=8,
                ),
                _record(
                    "claude-sonnet-5",
                    task_id,
                    outcome="success",
                    retries=1,
                    cost=0.20,
                    latency=16,
                ),
            ]
        )

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["evaluations"][0]
    assert result["quality_leader"] == "tie"
    assert result["retry_leader"] == "A"
    assert result["cost_leader"] == "A"
    assert result["latency_leader"] == "A"
    assert result["conclusion"] == "efficiency_lead"
    assert result["suggested_winner"] == "A"


def test_duplicate_attempt_for_one_side_is_excluded_as_ambiguous():
    records = _paired_records(
        ["success", "success", "success"],
        ["success", "success", "success"],
    )
    duplicate_task = "implementation-abc123def456-task-01"
    records.append(_record("gpt-5.6-terra", duplicate_task, outcome="failure"))

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["evaluations"][0]
    assert result["paired_tasks"] == 2
    assert result["ambiguous_task_ids"] == [duplicate_task]
    assert result["conclusion"] == "insufficient_evidence"


def test_unrelated_task_ids_do_not_enter_experiment():
    records = _paired_records(
        ["success", "success", "success"],
        ["success", "success", "success"],
    )
    records.extend(
        [
            _record("gpt-5.6-terra", "implementation-other-task-01"),
            _record("claude-sonnet-5", "implementation-other-task-01"),
        ]
    )

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["evaluations"][0]
    assert result["paired_tasks"] == 3
    assert result["paired_task_ids"] == [
        "implementation-abc123def456-task-01",
        "implementation-abc123def456-task-02",
        "implementation-abc123def456-task-03",
    ]
