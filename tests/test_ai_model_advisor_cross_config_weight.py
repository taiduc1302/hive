from tools.ai_model_advisor.feedback import (
    CROSS_CONFIG_WEIGHT,
    FeedbackStore,
    UsageRecord,
)


def test_cross_config_fallback_is_discounted_relative_to_exact_evidence():
    records = [
        UsageRecord(
            provider="openai",
            model_id="gpt-5.6-terra",
            effort="high",
            execution_mode="single",
            outcome="success",
            task_category="implementation",
        )
        for _ in range(6)
    ]
    feedback = FeedbackStore(records)

    exact = feedback.adjustment(
        "gpt-5.6-terra",
        "high",
        "single",
        "implementation",
    )
    fallback = feedback.adjustment(
        "gpt-5.6-terra",
        "medium",
        "single",
        "implementation",
    )

    assert feedback.evidence_scope(
        "gpt-5.6-terra",
        "high",
        "single",
        "implementation",
    ) == "exact"
    assert feedback.evidence_scope(
        "gpt-5.6-terra",
        "medium",
        "single",
        "implementation",
    ) == "cross_config"
    assert exact > 0
    assert fallback > 0
    assert fallback == round(exact * CROSS_CONFIG_WEIGHT, 3)
    assert fallback < exact


def test_sparse_other_config_history_does_not_create_fallback():
    feedback = FeedbackStore(
        [
            UsageRecord(
                provider="anthropic",
                model_id="claude-sonnet-5",
                effort="high",
                execution_mode="subagents",
                outcome="success",
                task_category="repo_review",
            )
            for _ in range(5)
        ]
    )

    assert feedback.evidence_scope(
        "claude-sonnet-5",
        "medium",
        "single",
        "repo_review",
    ) == "below_threshold"
    assert (
        feedback.adjustment(
            "claude-sonnet-5",
            "medium",
            "single",
            "repo_review",
        )
        == 0
    )
