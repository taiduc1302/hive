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
                "workload": {
                    "latency_sensitivity": 3.0,
                    "cost_sensitivity": 3.0,
                },
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
    for index, (a_outcome, b_outcome) in enumerate(
        zip(a_outcomes, b_outcomes, strict=True),
        start=1,
    ):
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
    result = report["results"][0]
    assert result["paired_tasks"] == 2
    assert result["decision"] == "insufficient_evidence"
    assert result["winner_side"] is None
    assert result["policy_ready"] is False
    assert report["policy_ready_experiments"] == 0


def test_quality_lead_takes_precedence_over_cheaper_challenger():
    records = _paired_records(
        ["success", "success", "success"],
        ["failure", "success", "partial"],
        a_cost=0.30,
        b_cost=0.05,
    )
    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["results"][0]

    assert result["paired_tasks"] == 3
    assert result["quality_delta_primary_minus_challenger"] > 0
    assert result["decision"] == "primary_leads"
    assert result["decision_basis"] == "outcome_quality"
    assert result["winner_side"] == "primary"
    assert result["policy_ready"] is True


def test_tied_quality_can_use_paired_efficiency():
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

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["results"][0]
    assert result["quality_delta_primary_minus_challenger"] == 0.05
    assert result["efficiency_delta_primary_advantage"] > 0
    assert result["decision"] == "primary_leads"
    assert result["decision_basis"] == "paired_efficiency"
    assert result["winner_side"] == "primary"


def test_duplicate_attempt_for_one_side_is_excluded_as_ambiguous():
    records = _paired_records(
        ["success", "success", "success"],
        ["success", "success", "success"],
    )
    duplicate_task = "implementation-abc123def456-task-01"
    records.append(_record("gpt-5.6-terra", duplicate_task, outcome="failure"))

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["results"][0]
    assert result["paired_tasks"] == 2
    assert result["ambiguous_task_ids"] == [duplicate_task]
    assert result["decision"] == "insufficient_evidence"
    assert result["policy_ready"] is False


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

    result = evaluate_experiment_plan(_plan(), FeedbackStore(records))["results"][0]
    assert result["paired_tasks"] == 3
    assert result["paired_task_ids"] == [
        "implementation-abc123def456-task-01",
        "implementation-abc123def456-task-02",
        "implementation-abc123def456-task-03",
    ]
