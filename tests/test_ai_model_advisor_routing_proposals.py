from __future__ import annotations

import json

from tools.ai_model_advisor.empirical_leaderboard import build_empirical_leaderboard
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord
from tools.ai_model_advisor.routing_proposals import (
    build_routing_proposals,
    main,
    routing_proposals_markdown,
)


def _matrix(primary_model: str = "model-b") -> list[dict[str, object]]:
    return [
        {
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
        }
    ]


def _record(model_id: str, outcome: str, task_id: str) -> UsageRecord:
    return UsageRecord(
        provider="test",
        model_id=model_id,
        effort="high",
        execution_mode="single",
        outcome=outcome,
        task_category="debugging",
        task_id=task_id,
    )


def _quality_leaderboard() -> dict[str, object]:
    records = [
        *[_record("model-a", "success", f"a-{i}") for i in range(3)],
        *[_record("model-b", "partial", f"b-{i}") for i in range(3)],
    ]
    return build_empirical_leaderboard(FeedbackStore(records))


def test_proposes_change_when_empirical_winner_differs_from_router() -> None:
    report = build_routing_proposals(_matrix("model-b"), _quality_leaderboard())
    proposal = report["proposals"][0]

    assert proposal["action"] == "propose_change"
    assert proposal["current"]["model_id"] == "model-b"
    assert proposal["candidate"]["model_id"] == "model-a"
    assert proposal["safe_to_apply"] is False
    assert proposal["requires_human_review"] is True


def test_keeps_route_when_current_primary_is_empirical_winner() -> None:
    report = build_routing_proposals(_matrix("model-a"), _quality_leaderboard())
    proposal = report["proposals"][0]

    assert proposal["action"] == "keep"
    assert proposal["requires_human_review"] is False
    assert proposal["candidate"]["model_id"] == "model-a"


def test_missing_empirical_category_is_insufficient() -> None:
    report = build_routing_proposals(_matrix(), {"records": 0, "categories": []})
    proposal = report["proposals"][0]

    assert proposal["action"] == "insufficient_evidence"
    assert proposal["candidate"] is None
    assert proposal["confidence"]["score"] == 0.0


def test_hold_decision_does_not_change_router() -> None:
    records = [
        *[_record("model-a", "success", f"a-{i}") for i in range(3)],
        *[_record("model-b", "success", f"b-{i}") for i in range(3)],
    ]
    leaderboard = build_empirical_leaderboard(FeedbackStore(records))
    report = build_routing_proposals(_matrix("model-b"), leaderboard)

    assert report["proposals"][0]["action"] == "keep"
    assert report["automatic_policy_mutation"] is False


def test_markdown_and_cli_emit_review_only_artifacts(tmp_path) -> None:
    feedback = tmp_path / "feedback.jsonl"
    for i in range(3):
        FeedbackStore.append(feedback, _record("model-a", "success", f"a-{i}"))
        FeedbackStore.append(feedback, _record("model-b", "partial", f"b-{i}"))

    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps({"routing_matrix": _matrix("model-b")}), encoding="utf-8")
    markdown_path = tmp_path / "proposals.md"
    json_path = tmp_path / "proposals.json"

    assert main([
        "--routing-matrix",
        str(matrix_path),
        "--feedback",
        str(feedback),
        "--output",
        str(markdown_path),
        "--json-output",
        str(json_path),
    ]) == 0

    markdown = markdown_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "Routing Proposals" in markdown
    assert "propose_change" in markdown
    assert payload["proposals"][0]["safe_to_apply"] is False
    assert payload["action_counts"]["propose_change"] == 1

    direct = routing_proposals_markdown(payload)
    assert direct.startswith("# AI Model Advisor Routing Proposals")
