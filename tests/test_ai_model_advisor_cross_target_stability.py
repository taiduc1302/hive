from __future__ import annotations

from importlib import import_module

import pytest

cross_target_stability = import_module("tools.ai_model_advisor.cross_target_stability")
execution_targets = import_module("tools.ai_model_advisor.execution_targets")
experiment_run = import_module("tools.ai_model_advisor.experiment_run")
feedback = import_module("tools.ai_model_advisor.feedback")
json = import_module("json")


EXPERIMENT_ID = "cross-debugging-01"
CATEGORY = "debugging"


def _plan() -> dict:
    return {
        "categories": [
            {
                "category": CATEGORY,
                "pairs": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "category": CATEGORY,
                        "kind": "execution_target_overhead",
                        "task_id_template": "cross-debugging-{nn}",
                        "primary": {
                            "provider": "openai",
                            "model_id": "gpt-5.6-sol",
                            "effort": "high",
                            "execution_mode": "hive_agent_loop",
                        },
                        "challenger": {
                            "provider": "openai",
                            "model_id": "gpt-5.6-sol",
                            "effort": "high",
                            "execution_mode": "hive_agent_loop_tool",
                        },
                    }
                ],
            }
        ],
        "cross_target_execution": {
            "schema_version": 1,
            "side_targets": {
                "A": execution_targets.profile_for_host("hive_agent_loop").binding(),
                "B": execution_targets.profile_for_host("hive_agent_loop_tool").binding(),
            },
        },
    }


def _record(
    side: str,
    index: int,
    *,
    latency: float,
    cost: float,
    outcome: str = "success",
) -> feedback.UsageRecord:
    execution_mode = "hive_agent_loop" if side == "A" else "hive_agent_loop_tool"
    task_id = f"cross-debugging-{index:02d}"
    return feedback.UsageRecord(
        provider="openai",
        model_id="gpt-5.6-sol",
        effort="high",
        execution_mode=execution_mode,
        outcome=outcome,
        latency_seconds=latency,
        cost_usd=cost,
        task_category=CATEGORY,
        task_id=task_id,
        source_id=f"benchmark:{EXPERIMENT_ID}:{task_id}:{side.lower()}",
    )


def test_cross_target_stability_reports_repeatable_overhead() -> None:
    records = []
    for index, a_latency in enumerate((1.0, 1.1, 0.9), start=1):
        records.extend(
            [
                _record("A", index, latency=a_latency, cost=0.010),
                _record("B", index, latency=a_latency * 1.5, cost=0.012),
            ]
        )

    report = cross_target_stability.build_cross_target_stability_report(
        _plan(),
        feedback.FeedbackStore(records),
        EXPERIMENT_ID,
    )

    assert report["status"] == "stable_overhead"
    assert report["matched_pairs"] == 3
    assert report["successful_latency_pairs"] == 3
    assert report["latency"]["median_overhead_ratio"] == pytest.approx(0.5)
    assert report["latency"]["mad"] == pytest.approx(0.0)
    assert report["cost"]["median_overhead_ratio"] == pytest.approx(0.2)
    assert report["policy"]["automatic_routing_mutation"] is False


def test_cross_target_stability_rejects_high_variance() -> None:
    b_latencies = (1.1, 2.0, 4.0)
    records = []
    for index, b_latency in enumerate(b_latencies, start=1):
        records.extend(
            [
                _record("A", index, latency=1.0, cost=0.010),
                _record("B", index, latency=b_latency, cost=0.012),
            ]
        )

    report = cross_target_stability.build_cross_target_stability_report(
        _plan(),
        feedback.FeedbackStore(records),
        EXPERIMENT_ID,
    )

    assert report["status"] == "unstable"
    assert "latency_overhead_unstable" in report["blockers"]


def test_cross_target_stability_requires_minimum_matched_pairs() -> None:
    records = []
    for index in (1, 2):
        records.extend(
            [
                _record("A", index, latency=1.0, cost=0.010),
                _record("B", index, latency=1.5, cost=0.012),
            ]
        )

    report = cross_target_stability.build_cross_target_stability_report(
        _plan(),
        feedback.FeedbackStore(records),
        EXPERIMENT_ID,
    )

    assert report["status"] == "insufficient_evidence"
    assert "matched_pairs<3" in report["blockers"]


def test_cross_target_stability_surfaces_failure_rate_regression() -> None:
    records = []
    for index in (1, 2, 3):
        records.extend(
            [
                _record("A", index, latency=1.0, cost=0.010),
                _record(
                    "B",
                    index,
                    latency=1.5,
                    cost=0.012,
                    outcome="failure" if index == 3 else "success",
                ),
            ]
        )

    report = cross_target_stability.build_cross_target_stability_report(
        _plan(),
        feedback.FeedbackStore(records),
        EXPERIMENT_ID,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["failure_rate"]["delta_b_minus_a"] == pytest.approx(1 / 3)
    assert "failure_rate_delta_too_large" in report["blockers"]


def test_cross_target_stability_rejects_duplicate_side_task_records() -> None:
    duplicate = _record("A", 1, latency=1.0, cost=0.010)
    records = [
        duplicate,
        duplicate,
        _record("B", 1, latency=1.5, cost=0.012),
    ]

    with pytest.raises(experiment_run.ExperimentRunnerError, match="Duplicate cross-target feedback"):
        cross_target_stability.build_cross_target_stability_report(
            _plan(),
            feedback.FeedbackStore(records),
            EXPERIMENT_ID,
            min_pairs=2,
        )


def test_cross_target_stability_ignores_other_task_category() -> None:
    records = []
    for index in (1, 2, 3):
        a = _record("A", index, latency=1.0, cost=0.010)
        b = _record("B", index, latency=1.5, cost=0.012)
        records.extend(
            [
                feedback.UsageRecord(**{**a.as_dict(), "task_category": "research"}),
                b,
            ]
        )

    report = cross_target_stability.build_cross_target_stability_report(
        _plan(),
        feedback.FeedbackStore(records),
        EXPERIMENT_ID,
    )

    assert report["status"] == "insufficient_evidence"
    assert report["matched_pairs"] == 0


def test_cross_target_stability_requires_overhead_kind() -> None:
    plan = _plan()
    plan["categories"][0]["pairs"][0]["kind"] = "model"

    with pytest.raises(
        experiment_run.ExperimentRunnerError,
        match="kind='execution_target_overhead'",
    ):
        cross_target_stability.build_cross_target_stability_report(
            plan,
            feedback.FeedbackStore(),
            EXPERIMENT_ID,
        )


def test_cross_target_stability_cli_writes_offline_artifacts(tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    feedback_path = tmp_path / "feedback.jsonl"
    markdown_path = tmp_path / "stability.md"
    json_path = tmp_path / "stability.json"

    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    records = []
    for index in (1, 2, 3):
        records.extend(
            [
                _record("A", index, latency=1.0, cost=0.010),
                _record("B", index, latency=1.5, cost=0.012),
            ]
        )
    feedback_path.write_text(
        "".join(json.dumps(record.as_dict()) + "\n" for record in records),
        encoding="utf-8",
    )

    status = cross_target_stability.main(
        [
            "--plan",
            str(plan_path),
            "--experiment-id",
            EXPERIMENT_ID,
            "--feedback",
            str(feedback_path),
            "--output",
            str(markdown_path),
            "--json-output",
            str(json_path),
        ]
    )

    assert status == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert report["status"] == "stable_overhead"
    assert report["matched_pairs"] == 3
    assert "Cross-Target Stability" in markdown
    assert "Provider calls" not in markdown
