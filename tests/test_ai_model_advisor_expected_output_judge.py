from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools.ai_model_advisor.expected_output_judge import (
    ExpectedOutputJudgeError,
    judge_expected_output,
    main,
)


def _payload(response_text=None):
    adapter_result = {}
    if response_text is not None:
        adapter_result["response_text"] = response_text
    return {
        "schema_version": 1,
        "experiment_id": "exp-1",
        "side": "A",
        "task_id": "task-01",
        "adapter_result": adapter_result,
    }


def test_strip_exact_ignores_outer_whitespace():
    result = judge_expected_output(
        _payload("  expected answer\n"),
        expected="expected answer",
        mode="strip-exact",
    )
    assert result["outcome"] == "success"
    assert "passed" in result["note"]


def test_exact_remains_strict():
    result = judge_expected_output(
        _payload("expected answer\n"),
        expected="expected answer",
        mode="exact",
    )
    assert result["outcome"] == "failure"


def test_contains_requires_fixed_substring():
    passed = judge_expected_output(
        _payload("prefix REQUIRED_TOKEN suffix"),
        expected="REQUIRED_TOKEN",
        mode="contains",
    )
    failed = judge_expected_output(
        _payload("prefix only"),
        expected="REQUIRED_TOKEN",
        mode="contains",
    )
    assert passed["outcome"] == "success"
    assert failed["outcome"] == "failure"


def test_json_equal_is_structural_not_format_sensitive():
    result = judge_expected_output(
        _payload('{"b": [2, 3], "a": 1}'),
        expected='{"a":1,"b":[2,3]}',
        mode="json-equal",
    )
    assert result["outcome"] == "success"


def test_invalid_candidate_json_is_model_failure():
    result = judge_expected_output(
        _payload("not-json"),
        expected='{"ok": true}',
        mode="json-equal",
    )
    assert result["outcome"] == "failure"
    assert "not valid JSON" in result["note"]


def test_invalid_expected_json_is_checker_configuration_error():
    with pytest.raises(ExpectedOutputJudgeError, match="fixture is invalid"):
        judge_expected_output(
            _payload('{"ok": true}'),
            expected="not-json",
            mode="json-equal",
        )


def test_missing_response_text_is_model_failure():
    result = judge_expected_output(
        _payload(),
        expected="answer",
        mode="strip-exact",
    )
    assert result["outcome"] == "failure"
    assert "did not contain response_text" in result["note"]


def test_cli_reads_expected_file_and_emits_schema_v1(tmp_path, monkeypatch, capsys):
    expected = tmp_path / "expected.txt"
    expected.write_text("answer\n", encoding="utf-8")
    payload = json.dumps(_payload("answer"))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))

    assert main(["--mode", "strip-exact", "--expected-file", str(expected)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema_version": 1,
        "outcome": "success",
        "note": "trimmed exact text check passed",
    }


def test_cli_bad_expected_fixture_exits_nonzero(tmp_path, monkeypatch, capsys):
    expected = tmp_path / "expected.json"
    expected.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "stdin",
        __import__("io").StringIO(json.dumps(_payload('{"ok": true}'))),
    )

    assert main(["--mode", "json-equal", "--expected-file", str(expected)]) == 2
    assert "fixture is invalid" in capsys.readouterr().err
