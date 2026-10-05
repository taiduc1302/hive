from __future__ import annotations

from importlib import import_module

import pytest

cross_target_decision = import_module("tools.ai_model_advisor.cross_target_decision")
json = import_module("json")


def _stability(
    *,
    status: str = "stable_overhead",
    latency: float | None = 0.20,
    cost: float | None = 0.10,
    failure_delta: float | None = 0.0,
) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": "cross-debugging-01",
        "category": "debugging",
        "kind": "execution_target_overhead",
        "status": status,
        "side_a_target": "hive_agent_loop",
        "side_b_target": "hive_agent_loop_tool",
        "matched_pairs": 5,
        "latency": {"median_overhead_ratio": latency},
        "cost": {"median_overhead_ratio": cost},
        "failure_rate": {"delta_b_minus_a": failure_delta},
    }


def test_decision_collects_more_when_stability_is_insufficient() -> None:
    report = cross_target_decision.build_cross_target_decision(
        _stability(status="insufficient_evidence")
    )

    assert report["decision"] == "collect_more"
    assert report["policy"]["safe_to_auto_apply"] is False


def test_decision_investigates_unstable_overhead() -> None:
    report = cross_target_decision.build_cross_target_decision(
        _stability(status="unstable")
    )

    assert report["decision"] == "investigate_instability"


def test_decision_prefers_no_tool_for_material_overhead_without_reliability_gain() -> None:
    report = cross_target_decision.build_cross_target_decision(
        _stability(latency=0.35, cost=0.05, failure_delta=0.0)
    )

    assert report["decision"] == "prefer_no_tool_for_equivalent_tasks"
    assert report["policy"]["scope"] == "equivalent_benchmark_tasks_only"
    assert report["policy"]["automatic_routing_mutation"] is False


def test_decision_preserves_material_reliability_tradeoff_for_human_review() -> None:
    report = cross_target_decision.build_cross_target_decision(
        _stability(latency=0.35, cost=0.20, failure_delta=-0.20)
    )

    assert report["decision"] == "manual_tradeoff_review"
    assert "failure-rate advantage" in report["rationale"]


def test_decision_accepts_tool_overhead_inside_budget() -> None:
    report = cross_target_decision.build_cross_target_decision(
        _stability(latency=0.10, cost=0.12, failure_delta=0.0)
    )

    assert report["decision"] == "tool_overhead_acceptable"


def test_decision_rejects_unexpected_target_direction() -> None:
    stability = _stability()
    stability["side_a_target"] = "hive_agent_loop_tool"
    stability["side_b_target"] = "hive_agent_loop"

    with pytest.raises(
        cross_target_decision.CrossTargetDecisionError,
        match="hive_agent_loop -> hive_agent_loop_tool",
    ):
        cross_target_decision.build_cross_target_decision(stability)


def test_decision_rejects_invalid_threshold() -> None:
    with pytest.raises(
        cross_target_decision.CrossTargetDecisionError,
        match="max_latency_overhead",
    ):
        cross_target_decision.build_cross_target_decision(
            _stability(),
            max_latency_overhead=1.5,
        )


def test_cross_target_decision_cli_writes_offline_artifacts(tmp_path) -> None:
    stability_path = tmp_path / "stability.json"
    markdown_path = tmp_path / "decision.md"
    json_path = tmp_path / "decision.json"
    stability_path.write_text(json.dumps(_stability()), encoding="utf-8")

    status = cross_target_decision.main(
        [
            "--stability",
            str(stability_path),
            "--output",
            str(markdown_path),
            "--json-output",
            str(json_path),
        ]
    )

    assert status == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert report["decision"] == "prefer_no_tool_for_equivalent_tasks"
    assert report["policy"]["safe_to_auto_apply"] is False
    assert "Cross-Target Decision" in markdown
