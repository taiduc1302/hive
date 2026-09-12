import json
from pathlib import Path

import pytest

from tools.ai_model_advisor.feedback import FeedbackStore
from tools.ai_model_advisor.hive_trace import (
    append_imported_feedback,
    import_hive_trace,
)
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _turn(
    execution_id: str,
    node_id: str,
    model: str,
    cost: float,
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cached_tokens: int = 0,
    cache_creation_tokens: int = 0,
    credits: float | None = None,
) -> dict:
    data = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cost_usd": cost,
    }
    if credits is not None:
        data["credits"] = credits
    return {
        "type": "llm_turn_complete",
        "stream_id": "queen",
        "node_id": node_id,
        "execution_id": execution_id,
        "data": data,
    }


def test_hive_trace_import_combines_cost_with_runtime_outcome(tmp_path):
    events = tmp_path / "events.jsonl"
    details = tmp_path / "details.jsonl"
    _write_jsonl(
        events,
        [
            _turn(
                "exec-1",
                "worker-a",
                "openai/gpt-5.6-terra",
                0.12,
                cached_tokens=40,
                cache_creation_tokens=10,
                credits=0.6,
            ),
            {
                "type": "node_retry",
                "stream_id": "worker:1",
                "node_id": "worker-a",
                "execution_id": "exec-1",
                "data": {"retry_count": 1},
            },
            _turn(
                "exec-1",
                "worker-a",
                "openai/gpt-5.6-terra",
                0.08,
                cached_tokens=60,
                cache_creation_tokens=5,
                credits=0.4,
            ),
            {
                "type": "judge_verdict",
                "stream_id": "worker:1",
                "node_id": "worker-a",
                "execution_id": "exec-1",
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    _write_jsonl(
        details,
        [
            {
                "node_id": "worker-a",
                "node_name": "Worker A",
                "node_type": "event_loop",
                "success": True,
                "exit_status": "success",
                "retry_count": 1,
                "latency_ms": 12500,
            }
        ],
    )

    report = import_hive_trace(
        events,
        ModelRegistry(REGISTRY),
        details_path=details,
        task_category="implementation",
    )

    assert len(report.records) == 1
    record = report.records[0]
    assert record.provider == "openai"
    assert record.model_id == "gpt-5.6-terra"
    assert record.outcome == "success"
    assert record.retries == 2
    assert record.latency_seconds == 12.5
    assert record.cost_usd == pytest.approx(0.20)
    assert record.input_tokens == 200
    assert record.output_tokens == 40
    assert record.cached_tokens == 100
    assert record.cache_creation_tokens == 15
    assert record.credits == pytest.approx(1.0)
    assert record.task_category == "implementation"
    assert record.effort == "observed"
    assert record.execution_mode == "hive_agent_loop"
    assert record.source_id == "hive:exec-1:worker-a"


def test_hive_trace_import_is_idempotent_by_source_id(tmp_path):
    events = tmp_path / "events.jsonl"
    feedback = tmp_path / "feedback.jsonl"
    _write_jsonl(
        events,
        [
            _turn("exec-2", "node-1", "anthropic/claude-opus-5", 0.5),
            {
                "type": "judge_verdict",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": "exec-2",
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    report = import_hive_trace(events, ModelRegistry(REGISTRY))
    first = append_imported_feedback(feedback, report.records)
    second = append_imported_feedback(feedback, report.records)

    assert first.appended == 1
    assert first.duplicates == 0
    assert second.appended == 0
    assert second.duplicates == 1
    assert len(FeedbackStore.load(feedback).records) == 1


def test_hive_trace_skips_mixed_model_node(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            _turn("exec-3", "node-1", "openai/gpt-5.6-terra", 0.1),
            _turn("exec-3", "node-1", "anthropic/claude-sonnet-5", 0.2),
            {
                "type": "judge_verdict",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": "exec-3",
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    report = import_hive_trace(events, ModelRegistry(REGISTRY))
    assert report.records == ()
    assert report.skipped_mixed_models == 1


def test_hive_trace_skips_unknown_model_without_guessing(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            _turn("exec-4", "node-1", "provider/mystery-frontier-99", 0.01),
            {
                "type": "judge_verdict",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": "exec-4",
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    report = import_hive_trace(events, ModelRegistry(REGISTRY))
    assert report.records == ()
    assert report.skipped_unknown_models == 1


def test_hive_trace_task_id_requires_single_node_filter(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_jsonl(events, [_turn("exec-5", "node-1", "openai/gpt-5.6-terra", 0.1)])
    with pytest.raises(ValueError, match="task-id requires --node-id"):
        import_hive_trace(
            events,
            ModelRegistry(REGISTRY),
            task_id="benchmark-1",
        )


def test_hive_trace_controlled_import_can_stamp_config_and_task_id(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            _turn("exec-6", "node-1", "openai/gpt-5.6-terra", 0.1),
            {
                "type": "judge_verdict",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": "exec-6",
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    report = import_hive_trace(
        events,
        ModelRegistry(REGISTRY),
        node_id="node-1",
        task_id="benchmark-1",
        task_category="implementation",
        effort="medium",
        execution_mode="single",
    )
    record = report.records[0]
    assert record.task_id == "benchmark-1"
    assert record.effort == "medium"
    assert record.execution_mode == "single"


def test_filtered_node_does_not_inherit_multi_node_execution_success(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            _turn("exec-7", "node-a", "openai/gpt-5.6-terra", 0.1),
            _turn("exec-7", "node-b", "openai/gpt-5.6-terra", 0.1),
            {
                "type": "execution_completed",
                "stream_id": "queen",
                "node_id": None,
                "execution_id": "exec-7",
                "data": {},
            },
        ],
    )
    report = import_hive_trace(
        events,
        ModelRegistry(REGISTRY),
        node_id="node-a",
    )
    assert report.records == ()
    assert report.skipped_unknown_outcomes == 1
