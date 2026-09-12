import json
from pathlib import Path

from tools.ai_model_advisor.feedback import FeedbackStore
from tools.ai_model_advisor.hive_history import (
    discover_hive_session_traces,
    import_hive_history,
)
from tools.ai_model_advisor.hive_trace import append_imported_feedback
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _successful_session(root: Path, agent: str, session_id: str, execution_id: str) -> Path:
    session = root / "agents" / agent / "sessions" / session_id
    _write_jsonl(
        session / "events.jsonl",
        [
            {
                "type": "llm_turn_complete",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": execution_id,
                "data": {
                    "model": "openai/gpt-5.6-terra",
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "cached_tokens": 8,
                    "cache_creation_tokens": 2,
                    "cost_usd": 0.02,
                    "credits": 0.4,
                },
            },
            {
                "type": "judge_verdict",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": execution_id,
                "data": {"action": "ACCEPT"},
            },
        ],
    )
    _write_jsonl(
        session / "logs" / "details.jsonl",
        [
            {
                "node_id": "node-1",
                "success": True,
                "exit_status": "success",
                "retry_count": 0,
                "latency_ms": 2000,
            }
        ],
    )
    return session


def test_bulk_discovery_only_accepts_session_level_event_logs(tmp_path):
    first = _successful_session(tmp_path, "agent-a", "session_one", "exec-1")
    second = _successful_session(tmp_path, "agent-b", "session_two", "exec-2")

    _write_jsonl(
        first / "workers" / "worker-1" / "events.jsonl",
        [{"type": "llm_turn_complete"}],
    )
    _write_jsonl(
        tmp_path / "random" / "events.jsonl",
        [{"type": "llm_turn_complete"}],
    )

    traces = discover_hive_session_traces(tmp_path)
    assert {trace.session_id for trace in traces} == {"session_one", "session_two"}
    assert {trace.events_path for trace in traces} == {
        first / "events.jsonl",
        second / "events.jsonl",
    }
    assert all(trace.details_path is not None for trace in traces)


def test_bulk_import_aggregates_sessions_and_namespaces_sources(tmp_path):
    _successful_session(tmp_path, "agent-a", "session_one", "shared-exec")
    _successful_session(tmp_path, "agent-b", "session_two", "shared-exec")

    report = import_hive_history(
        tmp_path,
        ModelRegistry(REGISTRY),
        task_category="implementation",
    )

    assert len(report.sessions) == 2
    assert len(report.records) == 2
    assert {record.source_id for record in report.records} == {
        "hive:session_one:shared-exec:node-1",
        "hive:session_two:shared-exec:node-1",
    }
    assert all(record.outcome == "success" for record in report.records)
    assert all(record.latency_seconds == 2.0 for record in report.records)
    assert all(record.input_tokens == 20 for record in report.records)
    assert all(record.output_tokens == 5 for record in report.records)
    assert all(record.cached_tokens == 8 for record in report.records)
    assert all(record.cache_creation_tokens == 2 for record in report.records)
    assert all(record.cost_usd == 0.02 for record in report.records)
    assert all(record.credits == 0.4 for record in report.records)


def test_bulk_import_can_be_applied_idempotently(tmp_path):
    _successful_session(tmp_path, "agent-a", "session_one", "exec-1")
    report = import_hive_history(tmp_path, ModelRegistry(REGISTRY))
    feedback = tmp_path / "advisor-feedback.jsonl"

    first = append_imported_feedback(feedback, report.records)
    second = append_imported_feedback(feedback, report.records)

    assert first.appended == 1
    assert second.appended == 0
    assert second.duplicates == 1
    assert len(FeedbackStore.load(feedback).records) == 1
