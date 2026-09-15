from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_JUDGE_SCHEMA_VERSION = 1
_VALID_MODES = {"exact", "strip-exact", "contains", "json-equal"}
_DEFAULT_MODE = "strip-exact"
_EXPECTED_FILE_ENV = "AI_MODEL_ADVISOR_EXPECTED_OUTPUT_FILE"
_MODE_ENV = "AI_MODEL_ADVISOR_EXPECTED_OUTPUT_MODE"


class ExpectedOutputJudgeError(ValueError):
    """Raised when the judge configuration/input cannot be evaluated safely."""


def _response_text(payload: dict[str, Any]) -> str | None:
    adapter_result = payload.get("adapter_result")
    if not isinstance(adapter_result, dict):
        raise ExpectedOutputJudgeError("judge payload adapter_result must be an object")
    value = adapter_result.get("response_text")
    return value if isinstance(value, str) else None


def _expected_text(args: argparse.Namespace) -> str:
    if args.expected_file is not None:
        return Path(args.expected_file).read_text(encoding="utf-8")
    if args.expected is not None:
        return args.expected
    env_file = os.getenv(_EXPECTED_FILE_ENV)
    if env_file:
        return Path(env_file).read_text(encoding="utf-8")
    raise ExpectedOutputJudgeError(
        f"provide --expected, --expected-file, or {_EXPECTED_FILE_ENV}"
    )


def _mode(args: argparse.Namespace) -> str:
    mode = args.mode or os.getenv(_MODE_ENV) or _DEFAULT_MODE
    if mode not in _VALID_MODES:
        raise ExpectedOutputJudgeError(
            f"mode must be one of {', '.join(sorted(_VALID_MODES))}"
        )
    return mode


def _result(outcome: str, note: str) -> dict[str, Any]:
    return {
        "schema_version": _JUDGE_SCHEMA_VERSION,
        "outcome": outcome,
        "note": note,
    }


def judge_expected_output(
    payload: dict[str, Any],
    *,
    expected: str,
    mode: str,
) -> dict[str, Any]:
    response = _response_text(payload)
    if response is None:
        return _result("failure", "adapter result did not contain response_text")

    if mode == "exact":
        matched = response == expected
        label = "exact text"
    elif mode == "strip-exact":
        matched = response.strip() == expected.strip()
        label = "trimmed exact text"
    elif mode == "contains":
        matched = expected in response
        label = "required substring"
    elif mode == "json-equal":
        try:
            expected_json = json.loads(expected)
        except json.JSONDecodeError as exc:
            raise ExpectedOutputJudgeError("expected JSON fixture is invalid") from exc
        try:
            response_json = json.loads(response)
        except json.JSONDecodeError:
            return _result("failure", "candidate output is not valid JSON")
        matched = response_json == expected_json
        label = "JSON structural equality"
    else:
        raise ExpectedOutputJudgeError(f"unsupported mode: {mode}")

    if matched:
        return _result("success", f"{label} check passed")
    return _result("failure", f"{label} check failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically judge adapter response_text against a fixed expected fixture"
    )
    parser.add_argument(
        "--mode",
        choices=sorted(_VALID_MODES),
        help=f"Comparison mode; defaults to {_MODE_ENV} or {_DEFAULT_MODE}",
    )
    expected = parser.add_mutually_exclusive_group(required=False)
    expected.add_argument("--expected", help="Expected text; prefer --expected-file for long/private fixtures")
    expected.add_argument("--expected-file", help="UTF-8 expected-output fixture")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ExpectedOutputJudgeError("judge payload must be a JSON object")
        result = judge_expected_output(
            payload,
            expected=_expected_text(args),
            mode=_mode(args),
        )
    except (ExpectedOutputJudgeError, OSError, json.JSONDecodeError) as exc:
        print(f"expected-output judge error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
