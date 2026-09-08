import json
import sys
from pathlib import Path

from tools.ai_model_advisor.activity import ActivityAnalyzer
from tools.ai_model_advisor.cli import build_parser
from tools.ai_model_advisor.experiment_plan import build_experiment_plan
from tools.ai_model_advisor.feedback import FeedbackStore
from tools.ai_model_advisor.recommend import RecommendationEngine
from tools.ai_model_advisor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tools" / "ai_model_advisor" / "registry.json"


def test_main_cli_forwards_deterministic_judge(tmp_path):
    profiles = ActivityAnalyzer().category_profiles_from_texts(
        ["Implement a backend API endpoint and update tests"]
    )
    plan = build_experiment_plan(
        profiles,
        RecommendationEngine(ModelRegistry(REGISTRY), FeedbackStore()),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    category = next(item for item in plan["categories"] if item["category"] == "implementation")
    pair = next(item for item in category["pairs"] if item["kind"] == "model")

    adapter = tmp_path / "adapter.py"
    judge = tmp_path / "judge.py"
    feedback = tmp_path / "feedback.jsonl"
    adapter.write_text(
        """import json, sys\np = json.load(sys.stdin)\nprint(json.dumps({'schema_version': 1, 'applied_configuration': p['configuration'], 'outcome': 'success', 'response_text': 'not accepted'}))\n""",
        encoding="utf-8",
    )
    judge.write_text(
        """import json, sys\np = json.load(sys.stdin)\nassert p['adapter_result']['response_text'] == 'not accepted'\nprint(json.dumps({'schema_version': 1, 'outcome': 'failure', 'note': 'main CLI judge ran'}))\n""",
        encoding="utf-8",
    )

    args = build_parser().parse_args(
        [
            "experiment-run",
            "--plan",
            str(plan_path),
            "--experiment-id",
            pair["experiment_id"],
            "--feedback",
            str(feedback),
            "--task",
            "fixed benchmark",
            "--apply",
            "--judge",
            sys.executable,
            str(judge),
            "--runner",
            sys.executable,
            str(adapter),
        ]
    )

    assert args.func(args) == 0
    records = FeedbackStore.load(feedback).records
    assert len(records) == 2
    assert {record.outcome for record in records} == {"failure"}
    assert all("judge: main CLI judge ran" in record.note for record in records)
