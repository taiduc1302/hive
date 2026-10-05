from __future__ import annotations

from importlib import import_module

import pytest

cross_target_portfolio = import_module("tools.ai_model_advisor.cross_target_portfolio")
json = import_module("json")


def _decision(
    category: str,
    decision: str,
    *,
    experiment_id: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id or f"exp-{category}",
        "category": category,
        "side_a_target": "hive_agent_loop",
        "side_b_target": "hive_agent_loop_tool",
        "decision": decision,
        "rationale": f"{category}: {decision}",
        "observed": {
            "median_latency_overhead_ratio": 0.2,
            "median_cost_overhead_ratio": 0.1,
            "failure_rate_delta_b_minus_a": 0.0,
            "matched_pairs": 5,
        },
        "policy": {
            "scope": "equivalent_benchmark_tasks_only",
            "safe_to_auto_apply": False,
            "automatic_routing_mutation": False,
            "automatic_hive_config_mutation": False,
        },
    }


def test_portfolio_preserves_category_isolation() -> None:
    report = cross_target_portfolio.build_cross_target_portfolio(
        [
            _decision("debugging", "prefer_no_tool_for_equivalent_tasks"),
            _decision("research", "tool_overhead_acceptable"),
        ]
    )

    assert report["portfolio_state"] == "ready_for_manual_category_review"
    assert report["category_count"] == 2
    assert report["policy"]["category_isolation"] is True
    assert report["policy"]["global_target_collapse"] is False
    rows = {row["category"]: row for row in report["categories"]}
    assert rows["debugging"]["manual_target_preference"] == "hive_agent_loop"
    assert rows["research"]["manual_target_preference"] is None


def test_portfolio_marks_unresolved_categories() -> None:
    report = cross_target_portfolio.build_cross_target_portfolio(
        [
            _decision("debugging", "prefer_no_tool_for_equivalent_tasks"),
            _decision("architecture", "collect_more"),
            _decision("research", "manual_tradeoff_review"),
        ]
    )

    assert report["portfolio_state"] == "mixed_evidence_requires_followup"
    assert report["unresolved_category_count"] == 2
    assert report["decision_counts"]["collect_more"] == 1
    assert report["decision_counts"]["manual_tradeoff_review"] == 1


def test_portfolio_rejects_duplicate_category() -> None:
    with pytest.raises(
        cross_target_portfolio.CrossTargetPortfolioError,
        match="duplicate decision category",
    ):
        cross_target_portfolio.build_cross_target_portfolio(
            [
                _decision("debugging", "collect_more", experiment_id="a"),
                _decision("debugging", "tool_overhead_acceptable", experiment_id="b"),
            ]
        )


def test_portfolio_rejects_auto_applicable_input() -> None:
    decision = _decision("debugging", "prefer_no_tool_for_equivalent_tasks")
    decision["policy"]["safe_to_auto_apply"] = True

    with pytest.raises(
        cross_target_portfolio.CrossTargetPortfolioError,
        match="non-auto-applicable",
    ):
        cross_target_portfolio.build_cross_target_portfolio([decision])


def test_portfolio_rejects_wrong_execution_target() -> None:
    decision = _decision("debugging", "collect_more")
    decision["side_b_target"] = "provider_api"

    with pytest.raises(
        cross_target_portfolio.CrossTargetPortfolioError,
        match="side_b_target",
    ):
        cross_target_portfolio.build_cross_target_portfolio([decision])


def test_portfolio_requires_at_least_one_decision() -> None:
    with pytest.raises(
        cross_target_portfolio.CrossTargetPortfolioError,
        match="at least one decision",
    ):
        cross_target_portfolio.build_cross_target_portfolio([])


def test_cross_target_portfolio_cli_writes_offline_artifacts(tmp_path) -> None:
    debug_path = tmp_path / "debugging.json"
    research_path = tmp_path / "research.json"
    markdown_path = tmp_path / "portfolio.md"
    json_path = tmp_path / "portfolio.json"
    debug_path.write_text(
        json.dumps(_decision("debugging", "prefer_no_tool_for_equivalent_tasks")),
        encoding="utf-8",
    )
    research_path.write_text(
        json.dumps(_decision("research", "tool_overhead_acceptable")),
        encoding="utf-8",
    )

    status = cross_target_portfolio.main(
        [
            "--decision",
            str(debug_path),
            "--decision",
            str(research_path),
            "--output",
            str(markdown_path),
            "--json-output",
            str(json_path),
        ]
    )

    assert status == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert report["portfolio_state"] == "ready_for_manual_category_review"
    assert report["category_count"] == 2
    assert report["policy"]["safe_to_auto_apply"] is False
    assert "Cross-Target Portfolio" in markdown
