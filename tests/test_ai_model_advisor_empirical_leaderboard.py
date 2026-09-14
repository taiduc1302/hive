from __future__ import annotations

import json

from tools.ai_model_advisor.empirical_leaderboard import (
    build_empirical_leaderboard,
    empirical_leaderboard_markdown,
    main,
)
from tools.ai_model_advisor.feedback import FeedbackStore, UsageRecord


def _record(
    model_id: str,
    outcome: str,
    task_id: str,
    *,
    category: str = "debugging",
    effort: str = "high",
    execution_mode: str = "single",
    retries: int = 0,
    latency: float | None = None,
    cost: float | None = None,
) -> UsageRecord:
    return UsageRecord(
        provider="test",
        model_id=model_id,
        effort=effort,
        execution_mode=execution_mode,
        outcome=outcome,
        retries=retries,
        latency_seconds=latency,
        cost_usd=cost,
        task_category=category,
        task_id=task_id,
    )


def test_leaderboard_promotes_clear_quality_winner() -> None:
    records = [
        *[_record("model-a", "success", f"a-{index}") for index in range(3)],
        *[_record("model-b", "partial", f"b-{index}") for index in range(3)],
    ]

    report = build_empirical_leaderboard(FeedbackStore(records))
    category = report["categories"][0]
    decision = category["decision"]

    assert decision["status"] == "promote"
    assert decision["winner"]["model_id"] == "model-a"
    assert decision["runner_up"]["model_id"] == "model-b"
    assert decision["quality_margin"] >= 1.0


def test_leaderboard_holds_when_evidence_is_close() -> None:
    records = [
        *[_record("model-a", "success", f"a-{index}") for index in range(3)],
        *[_record("model-b", "success", f"b-{index}") for index in range(3)],
    ]

    report = build_empirical_leaderboard(FeedbackStore(records))
    decision = report["categories"][0]["decision"]

    assert decision["status"] == "hold"
    assert decision["score_margin"] == 0.0


def test_leaderboard_requires_two_exact_controlled_configs() -> None:
    records = [_record("model-a", "success", f"task-{index}") for index in range(3)]

    report = build_empirical_leaderboard(FeedbackStore(records))
    decision = report["categories"][0]["decision"]

    assert decision["status"] == "insufficient_evidence"
    assert decision["winner"]["model_id"] == "model-a"
    assert decision["runner_up"] is None


def test_uncontrolled_hive_history_cannot_win_leaderboard() -> None:
    controlled = [_record("model-a", "success", f"a-{index}") for index in range(3)]
    uncontrolled = [
        _record(
            "model-historical",
            "success",
            f"h-{index}",
            effort="observed",
            execution_mode="hive_agent_loop",
        )
        for index in range(10)
    ]

    report = build_empirical_leaderboard(FeedbackStore([*controlled, *uncontrolled]))
    category = report["categories"][0]

    assert report["excluded_uncontrolled_configurations"] == 1
    assert [row["model_id"] for row in category["configurations"]] == ["model-a"]
    assert category["decision"]["status"] == "insufficient_evidence"


def test_paired_efficiency_can_promote_when_quality_is_not_worse() -> None:
    records: list[UsageRecord] = []
    for index in range(3):
        task_id = f"paired-{index}"
        records.append(
            _record(
                "model-a",
                "success",
                task_id,
                latency=10.0,
                cost=0.10,
            )
        )
        records.append(
            _record(
                "model-b",
                "success",
                task_id,
                latency=30.0,
                cost=0.30,
            )
        )

    report = build_empirical_leaderboard(FeedbackStore(records))
    decision = report["categories"][0]["decision"]

    assert decision["winner"]["model_id"] == "model-a"
    assert decision["winner"]["efficiency_ready"] is True
    assert decision["runner_up"]["efficiency_ready"] is True
    assert decision["status"] == "promote"


def test_markdown_and_cli_emit_decision_artifacts(tmp_path) -> None:
    feedback = tmp_path / "feedback.jsonl"
    for index in range(3):
        FeedbackStore.append(feedback, _record("model-a", "success", f"a-{index}"))
        FeedbackStore.append(feedback, _record("model-b", "partial", f"b-{index}"))

    markdown_path = tmp_path / "leaderboard.md"
    json_path = tmp_path / "leaderboard.json"
    assert (
        main(
            [
                "--feedback",
                str(feedback),
                "--output",
                str(markdown_path),
                "--json-output",
                str(json_path),
            ]
        )
        == 0
    )

    markdown = markdown_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "Empirical Leaderboard" in markdown
    assert "Decision: **promote**" in markdown
    assert payload["categories"][0]["decision"]["winner"]["model_id"] == "model-a"

    direct = empirical_leaderboard_markdown(
        build_empirical_leaderboard(FeedbackStore.load(feedback))
    )
    assert direct.startswith("# AI Model Advisor Empirical Leaderboard")
