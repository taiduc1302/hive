from __future__ import annotations

import pytest

from tools.ai_model_advisor.cli import build_parser
from tools.ai_model_advisor.promotion_review import build_promotion_review


def _matrix(primary_model: str = "model-b"):
    return [
        {
            "category": "debugging",
            "primary": {
                "provider": "test",
                "model_id": primary_model,
                "label": primary_model,
                "effort": "high",
                "execution_mode": "single",
            },
            "alternatives": [],
        }
    ]


def _evaluation(state: str = "eligible_for_manual_promotion"):
    return {
        "automatic_policy_mutation": False,
        "automatic_rollback": False,
        "evaluations": [
            {
                "category": "debugging",
                "state": state,
                "current": {
                    "provider": "test",
                    "model_id": "model-b",
                    "effort": "high",
                    "execution_mode": "single",
                },
                "candidate": {
                    "provider": "test",
                    "model_id": "model-a",
                    "effort": "high",
                    "execution_mode": "single",
                },
                "required_pairs": 3,
                "matched_pairs": 3,
                "leaderboard_status": "promote",
                "winner_matches_candidate": True,
                "safe_to_apply": False,
                "requires_human_approval": state == "eligible_for_manual_promotion",
            }
        ],
    }


def test_eligible_canary_builds_manual_change_package():
    report = build_promotion_review(_evaluation(), _matrix())
    item = report["reviews"][0]

    assert item["state"] == "ready_for_manual_edit"
    assert item["manual_change"]["before"]["model_id"] == "model-b"
    assert item["manual_change"]["after"]["model_id"] == "model-a"
    assert item["manual_change"]["rollback_to"]["model_id"] == "model-b"
    assert item["manual_change"]["change_id"].startswith("debugging-")
    assert item["safe_to_apply"] is False
    assert item["requires_human_approval"] is True
    assert report["automatic_policy_mutation"] is False
    assert report["ready_changes"] == 1


def test_route_drift_blocks_stale_manual_promotion():
    item = build_promotion_review(_evaluation(), _matrix("model-c"))["reviews"][0]

    assert item["state"] == "blocked_route_drift"
    assert item["manual_change"] is None
    assert item["safe_to_apply"] is False


def test_rollback_canary_is_never_packaged_as_promotion():
    evaluation = _evaluation("rollback_candidate")
    evaluation["evaluations"][0]["requires_human_approval"] = False

    item = build_promotion_review(evaluation, _matrix())["reviews"][0]

    assert item["state"] == "do_not_promote"
    assert item["manual_change"] is None


def test_inconsistent_eligible_payload_fails_closed():
    evaluation = _evaluation()
    evaluation["evaluations"][0]["safe_to_apply"] = True

    item = build_promotion_review(evaluation, _matrix())["reviews"][0]

    assert item["state"] == "blocked_inconsistent_canary"
    assert item["manual_change"] is None


def test_duplicate_routing_categories_are_rejected():
    matrix = [*_matrix(), *_matrix()]
    with pytest.raises(ValueError, match="duplicate category"):
        build_promotion_review(_evaluation(), matrix)


def test_central_cli_exposes_promotion_review_command():
    args = build_parser().parse_args(
        [
            "promotion-review",
            "--canary-evaluation",
            "canary.json",
            "--routing-matrix",
            "matrix.json",
        ]
    )
    assert args.func.__name__ == "command_promotion_review"
