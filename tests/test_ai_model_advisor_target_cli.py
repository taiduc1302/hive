from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_target_recommend_cli_writes_executable_report(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "coding": 4.5,
                "reasoning": 4.2,
                "agentic": 4.6,
                "ambiguity": 3.8,
                "breadth": 5.0,
                "parallelism": 5.0,
                "latency_sensitivity": 2.0,
                "cost_sensitivity": 2.0,
                "volume": 3.0,
                "categories": {"implementation": 30},
                "activity_count": 30,
            }
        ),
        encoding="utf-8",
    )
    markdown = tmp_path / "target.md"
    payload = tmp_path / "target.json"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.ai_model_advisor.target_recommend",
            "--profile",
            str(profile),
            "--target",
            "hive",
            "--top",
            "4",
            "--output",
            str(markdown),
            "--json-output",
            str(payload),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    text = markdown.read_text(encoding="utf-8")
    data = json.loads(payload.read_text(encoding="utf-8"))

    assert "Target-Aware AI Model Recommendation" in text
    assert data["execution_target"]["host"] == "hive"
    assert data["recommendations"]
    assert all(item["execution_mode"] == "single" for item in data["recommendations"])
    assert all(item["preferred_execution_mode"] == "single" for item in data["recommendations"])
