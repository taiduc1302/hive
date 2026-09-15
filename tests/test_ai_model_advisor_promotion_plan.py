from tools.ai_model_advisor.cli import build_parser
from tools.ai_model_advisor.empirical_leaderboard import build_empirical_leaderboard
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.promotion_plan import build_promotion_plans, promotion_plans_markdown
from tools.ai_model_advisor.routing_proposals import build_routing_proposals


def _matrix(primary_model="model-b"):
    return [{
        "category": "debugging",
        "activity_count": 12,
        "primary": {
            "provider": "test",
            "model_id": primary_model,
            "label": primary_model,
            "effort": "high",
            "execution_mode": "single",
            "score": 10.0,
            "confidence": 0.75,
        },
        "alternatives": [],
        "workload": {},
    }]


def _record(model_id, outcome, task_id):
    return UsageRecord(
        provider="test",
        model_id=model_id,
        effort="high",
        execution_mode="single",
        outcome=outcome,
        task_category="debugging",
        task_id=task_id,
    )


def _leaderboard():
    records = [
        *[_record("model-a", "success", f"a-{i}") for i in range(3)],
        *[_record("model-b", "partial", f"b-{i}") for i in range(3)],
    ]
    return build_empirical_leaderboard(FeedbackStore(records))


def test_change_proposal_builds_review_only_canary_plan():
    leaderboard = _leaderboard()
    proposals = build_routing_proposals(_matrix("model-b"), leaderboard)
    report = build_promotion_plans(proposals, leaderboard)
    plan = report["plans"][0]
    assert plan["state"] == "ready_for_canary"
    assert plan["current"]["model_id"] == "model-b"
    assert plan["candidate"]["model_id"] == "model-a"
    assert plan["recommended_paired_trials"] >= 3
    assert plan["safe_to_apply"] is False
    assert plan["requires_human_approval"] is True
    assert report["automatic_policy_mutation"] is False
    assert report["automatic_canary_execution"] is False


def test_keep_route_needs_no_canary():
    leaderboard = _leaderboard()
    proposals = build_routing_proposals(_matrix("model-a"), leaderboard)
    report = build_promotion_plans(proposals, leaderboard)
    plan = report["plans"][0]
    assert plan["state"] == "no_change"
    assert plan["recommended_paired_trials"] == 0


def test_markdown_contains_acceptance_and_rollback_contract():
    leaderboard = _leaderboard()
    proposals = build_routing_proposals(_matrix("model-b"), leaderboard)
    markdown = promotion_plans_markdown(build_promotion_plans(proposals, leaderboard))
    assert "Promotion / Canary Plans" in markdown
    assert "Acceptance criteria" in markdown
    assert "Rollback / stop criteria" in markdown
    assert "safe_to_apply" in markdown


def test_central_cli_exposes_promotion_plan_command():
    args = build_parser().parse_args([
        "promotion-plan",
        "--routing-matrix",
        "matrix.json",
        "--feedback",
        "feedback.jsonl",
    ])
    assert args.func.__name__ == "command_promotion_plan"
