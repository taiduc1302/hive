from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cross_target_experiment import (
    build_agent_loop_overhead_plan,
    cross_target_profiles,
)
from .expected_output_judge import ExpectedOutputJudgeError, judge_expected_output
from .experiment_judge import apply_outcome_judge
from .experiment_run import (
    ExperimentRunnerError,
    RunnerExecutor,
    RunnerInfrastructureError,
    append_pair_feedback,
    command_executor,
    experiment_run_markdown,
    pair_run_json,
    run_experiment_pair,
)
from .experiment_run_cli import build_preview
from .feedback import FeedbackStore

ExecutorFactory = Callable[[list[str], float], RunnerExecutor]


def _load_json_object(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExperimentRunnerError("Experiment plan root must be a JSON object")
    return data


def _task_text(args: argparse.Namespace) -> str:
    if args.task is not None and args.task_file is not None:
        raise ExperimentRunnerError("Use only one of --task or --task-file")
    if args.task_file is not None:
        task = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task is not None:
        task = args.task
    else:
        raise ExperimentRunnerError("Provide --task or --task-file")
    if not task.strip():
        raise ExperimentRunnerError("Benchmark task must not be empty")
    return task


def _expected_judge(path: str | Path, mode: str):
    try:
        expected = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ExperimentRunnerError(f"could not read expected-output fixture: {exc}") from exc
    if mode == "json-equal":
        try:
            json.loads(expected)
        except json.JSONDecodeError as exc:
            raise ExperimentRunnerError("expected-output JSON fixture is invalid") from exc

    def judge(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return judge_expected_output(payload, expected=expected, mode=mode)
        except ExpectedOutputJudgeError as exc:
            raise RunnerInfrastructureError(
                f"expected-output judge failed: {exc}"
            ) from exc

    return judge


def build_side_dispatch_executor(
    plan: dict[str, Any],
    timeout_seconds: float,
    *,
    executor_factory: ExecutorFactory = command_executor,
) -> RunnerExecutor:
    """Build a fail-closed dispatcher using each side's canonical target runner."""
    profiles = cross_target_profiles(plan)
    executors = {
        side: executor_factory(
            [sys.executable, "-m", profile.runner_module],
            timeout_seconds,
        )
        for side, profile in profiles.items()
    }

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        side = str(payload.get("side") or "")
        if side not in executors:
            raise RunnerInfrastructureError(f"cross-target payload has invalid side {side!r}")
        configuration = payload.get("configuration")
        if not isinstance(configuration, dict):
            raise RunnerInfrastructureError("cross-target payload configuration is missing")
        profile = profiles[side]
        if configuration.get("execution_mode") not in profile.execution_modes:
            raise RunnerInfrastructureError(
                f"side {side} execution_mode does not match target {profile.host}"
            )
        adapter_payload = dict(payload)
        adapter_payload["acceptance_mode"] = "external_judge"
        return executors[side](adapter_payload)

    return execute


def _with_expected_judge(
    executor: RunnerExecutor,
    expected_output_file: str | Path,
    expected_output_mode: str,
) -> RunnerExecutor:
    judge = _expected_judge(expected_output_file, expected_output_mode)

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        adapter_result = executor(payload)
        return apply_outcome_judge(payload, adapter_result, judge)

    return execute


def cross_target_markdown(
    plan: dict[str, Any],
    report: Any,
    *,
    applied: bool,
) -> str:
    profiles = cross_target_profiles(plan)
    prefix = [
        "# AI Model Advisor Cross-Target Experiment",
        "",
        f"Side A target: **{profiles['A'].host}**",
        f"Side B target: **{profiles['B'].host}**",
        "",
        (
            "Both sides use the same provider/model/effort and differ only in "
            "execution target/mode."
        ),
        "",
    ]
    base = experiment_run_markdown(report, applied=applied)
    return "\n".join(prefix) + "\n" + base


def _write_text(path: str | None, text: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="" if text.endswith("\n") else "\n")


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare controlled Hive AgentLoop no-tool vs single-tool execution "
            "on the same model/effort and benchmark task."
        )
    )
    parser.add_argument("--plan", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--feedback", required=True)
    parser.add_argument("--task")
    parser.add_argument("--task-file")
    parser.add_argument("--source-side", choices=("A", "B"), default="A")
    parser.add_argument("--task-id")
    parser.add_argument("--order", choices=("auto", "ab", "ba"), default="auto")
    parser.add_argument("--allow-ready", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--expected-output-file")
    parser.add_argument(
        "--expected-output-mode",
        choices=("exact", "strip-exact", "contains", "json-equal"),
        default="strip-exact",
    )
    parser.add_argument("--derived-plan-output")
    parser.add_argument("--output")
    parser.add_argument("--json-output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source_plan = _load_json_object(args.plan)
        derived = build_agent_loop_overhead_plan(
            source_plan,
            args.experiment_id,
            source_side=args.source_side,
        )
        if args.derived_plan_output:
            _write_json(args.derived_plan_output, derived)

        task = _task_text(args)
        feedback = FeedbackStore.load(args.feedback)
        preview = build_preview(
            derived,
            feedback,
            args.experiment_id,
            task,
            task_id=args.task_id,
            order=args.order,
            allow_ready=args.allow_ready,
        )
        preview["cross_target_execution"] = derived["cross_target_execution"]

        if not args.apply:
            _write_text(
                args.output,
                "# AI Model Advisor Cross-Target Preview\n\n"
                + f"Experiment: `{args.experiment_id}`\n"
                + f"Task ID: `{preview['task_id']}`\n"
                + "Provider calls: **no**\n",
            )
            _write_json(args.json_output, preview)
            return 0

        if not args.expected_output_file:
            raise ExperimentRunnerError(
                "--apply requires --expected-output-file so benchmark success is "
                "independently and deterministically judged"
            )

        executor = _with_expected_judge(
            build_side_dispatch_executor(derived, args.timeout_seconds),
            args.expected_output_file,
            args.expected_output_mode,
        )

        live_feedback = FeedbackStore.load(args.feedback)
        build_preview(
            derived,
            live_feedback,
            args.experiment_id,
            task,
            task_id=preview["task_id"],
            order=args.order,
            allow_ready=args.allow_ready,
        )
        report = run_experiment_pair(
            derived,
            live_feedback,
            args.experiment_id,
            task,
            executor,
            task_id=preview["task_id"],
            order=args.order,
            allow_ready=args.allow_ready,
        )
        append_pair_feedback(args.feedback, report)
        _write_text(
            args.output,
            cross_target_markdown(derived, report, applied=True),
        )
        payload = pair_run_json(report, applied=True)
        payload["cross_target_execution"] = derived["cross_target_execution"]
        _write_json(args.json_output, payload)
        return 0
    except (
        ExperimentRunnerError,
        RunnerInfrastructureError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"cross-target experiment error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
