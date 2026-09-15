import json
from pathlib import Path

from tools.ai_model_advisor.hive_trace import import_hive_trace
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def test_success_flag_does_not_override_paused_runtime_status(tmp_path):
    events = tmp_path / "events.jsonl"
    details = tmp_path / "details.jsonl"
    events.write_text(
        json.dumps(
            {
                "type": "llm_turn_complete",
                "stream_id": "queen",
                "node_id": "node-1",
                "execution_id": "exec-paused",
                "data": {
                    "model": "openai/gpt-5.6-terra",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cost_usd": 0.01,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    details.write_text(
        json.dumps(
            {
                "node_id": "node-1",
                "success": True,
                "exit_status": "paused",
                "retry_count": 0,
                "latency_ms": 1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = import_hive_trace(
        events,
        ModelRegistry(REGISTRY),
        details_path=details,
    )
    assert len(report.records) == 1
    assert report.records[0].outcome == "partial"
