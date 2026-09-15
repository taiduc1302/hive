import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "ai-model-advisor"
SKILL_MD = SKILL_DIR / "SKILL.md"


def test_skill_bundle_has_required_metadata_and_live_references():
    text = SKILL_MD.read_text(encoding="utf-8")
    frontmatter = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, flags=re.DOTALL)
    assert frontmatter, "SKILL.md must start with YAML frontmatter"

    metadata = frontmatter.group("body")
    assert re.search(r"(?m)^name:\s*ai-model-advisor\s*$", metadata)
    assert re.search(r"(?m)^description:\s*\S.+$", metadata)
    assert (SKILL_DIR / "agents" / "openai.yaml").is_file()

    bundled_links = set(re.findall(r"`((?:references|scripts)/[^`\s]+)`", text))
    assert bundled_links, "SKILL.md should point to its bundled progressive-disclosure resources"
    for relative_path in bundled_links:
        assert (SKILL_DIR / relative_path).is_file(), f"Missing bundled Skill resource: {relative_path}"


def test_skill_agent_metadata_exposes_required_ui_fields():
    text = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for field in ("display_name:", "short_description:", "default_prompt:"):
        assert field in text


def test_summarize_activity_script_generic_smoke(tmp_path):
    source = tmp_path / "activity.json"
    source.write_text(
        json.dumps(
            {
                "activities": [
                    {"title": "Implement endpoint"},
                    {"title": "Investigate latest model release"},
                    {"title": "Run automation workflow"},
                    {"title": "Review all files"},
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "summarize_activity.py"), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["items"] == 4
    assert payload["categories"]["coding"] == 1
    assert payload["categories"]["research"] == 1
    assert payload["categories"]["automation"] == 1
    assert payload["categories"]["broad_scope"] == 1


def test_summarize_activity_script_chatgpt_ignores_assistant_messages(tmp_path):
    source = tmp_path / "conversations.json"
    source.write_text(
        json.dumps(
            [
                {
                    "title": "Repo review",
                    "mapping": {
                        "user": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["Debug the repository"]},
                            }
                        },
                        "assistant": {
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["Run automation workflow"]},
                            }
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "summarize_activity.py"),
            str(source),
            "--format",
            "chatgpt",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["items"] == 2
    assert payload["categories"]["coding"] == 2
    assert "automation" not in payload["categories"]
