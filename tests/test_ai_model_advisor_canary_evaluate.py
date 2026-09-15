from tools.ai_model_advisor.canary_evaluate import evaluate_promotion_canary
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord


def _plan(required_pairs=3):
    return {
        "plans": [
            {
                "category": "debugging",
                "state": "ready_for_canary",
                "current": {
                    "model_id": "model-b",
                    "effort": "high",
                    "execution_mode": "single",
                },
                "candidate": {
                    "model_id": "model-a",
                    "effort": "high",
                    "execution_mode": "single",
                },
                "recommended_paired_trials": required_pairs,
            }
        ]
    }


def _record(model, outcome, task_id):
    return UsageRecord(
        provider="test",
        model_id=model,
        effort="high",
        execution_mode="single",
        outcome=outcome,
        task_category="debugging",
        task_id=task_id,
    )


def test_canary_can_become_eligible_for_manual_promotion():
    records = []
    for i in range(3):
        task_id = f"canary-{i}"
        records.append(_record("model-a", "success", task_id))
        records.append(_record("model-b", "partial", task_id))

    report = evaluate_promotion_canary(_plan(), FeedbackStore(records))
    item = report["evaluations"][0]

    assert item["state"] == "eligible_for_manual_promotion"
    assert item["matched_pairs"] == 3
    assert item["winner_matches_candidate"] is True
    assert item["safe_to_apply"] is False
    assert item["requires_human_approval"] is True
    assert report["automatic_policy_mutation"] is False


def test_canary_continues_until_required_matched_pairs_exist():
    records = []
    for i in range(2):
        task_id = f"canary-{i}"
        records.append(_record("model-a", "success", task_id))
        records.append(_record("model-b", "partial", task_id))

    item = evaluate_promotion_canary(_plan(required_pairs=3), FeedbackStore(records))["evaluations"][0]
    assert item["state"] == "continue_canary"
    assert item["matched_pairs"] == 2


def test_canary_rolls_back_on_material_failure_regression():
    records = []
    for i in range(3):
        task_id = f"canary-{i}"
        records.append(_record("model-a", "failure", task_id))
        records.append(_record("model-b", "success", task_id))

    item = evaluate_promotion_canary(_plan(), FeedbackStore(records))["evaluations"][0]
    assert item["state"] == "rollback_candidate"
    assert item["failure_rate_regression"] == 1.0
    assert item["safe_to_apply"] is False


def test_unmatched_tasks_do_not_count_as_canary_pairs():
    records = [
        _record("model-a", "success", "a-only"),
        _record("model-b", "partial", "b-only"),
    ]
    item = evaluate_promotion_canary(_plan(), FeedbackStore(records))["evaluations"][0]
    assert item["state"] == "continue_canary"
    assert item["matched_pairs"] == 0
