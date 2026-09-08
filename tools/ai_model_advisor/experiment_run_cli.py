from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .experiment_run import (
    ExperimentRunnerError,
    append_pair_feedback,
    command_executor,
    ensure_experiment_collectable,
    execution_order,
    experiment_run_markdown,
    find_experiment,
    next_task_id,
    pair_run_json,
    run_experiment_pair,
    runner_payload,
    task_sha256,
)
from .feedback import EXACT_FEEDBACK_MIN, FeedbackStore


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


def _redacted_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return audit metadata without persisting the benchmark prompt itself."""
    return {key: value for key, value in payload.items() if key != "task"}


def build_preview(
    plan: dict[str, Any],
    feedback: FeedbackStore,
    experiment_id: str,
    task: str,
    *,
    task_id: str | None = None,
    order: str = "auto",
    allow_ready: bool = False,
) -> dict[str, Any]:
    pair = find_experiment(plan, experiment_id)
    complete_task_ids = ensure_experiment_collectable(
        pair,
        feedback,
        allow_ready=allow_ready,
    )
    if task_id is None:
        task_id, _ = next_task_id(pair, feedback)
    run_order = execution_order(pair, task_id, order)
    payloads = {
        side: _redacted_preview_payload(
            runner_payload(pair, experiment_id, side, task_id, task)
        )
        for side in ("A", "B")
    }
    source_ids = [
        f"benchmark:{experiment_id}:{task_id}:a",
        f"benchmark:{experiment_id}:{task_id}:b",
    ]
    duplicates = sorted(set(source_ids) & feedback.source_ids)
    if duplicates:
        raise ExperimentRunnerError(
            "Benchmark source ID already exists: " + ", ".join(duplicates)
        )
    return {
        "mode": "preview",
        "experiment_id": experiment_id,
        "category": pair["category"],
        "kind": pair["kind"],
        "saved_status": pair.get("status"),
        "live_complete_pair_task_ids": list(complete_task_ids),
        "live_complete_pairs": len(complete_task_ids),
        "quality_threshold": EXACT_FEEDBACK_MIN,
        "task_id": task_id,
        "task_sha256": task_sha256(task),
        "task_text_included": False,
        "order": list(run_order),
        "source_ids": source_ids,
        "payloads": payloads,
    }


def preview_markdown(preview: dict[str, Any]) -> str:
    lines = [
        "# AI Model Advisor Experiment Run Preview",
        "",
        "No executable was launched and no feedback was written.",
        "Benchmark task text is intentionally omitted from preview artifacts; only its SHA-256 is retained.",
        "",
        f"Experiment: `{preview['experiment_id']}`",
        f"Category: **{preview['category']}**",
        f"Kind: **{preview['kind']}**",
        f"Saved status: **{preview.get('saved_status') or 'unknown'}**",
        (
            f"Live complete A/B pairs: **{preview['live_complete_pairs']} / "
            f"{preview['quality_threshold']}**"
        ),
        f"Task ID: `{preview['task_id']}`",
        f"Task SHA-256: `{preview['task_sha256']}`",
        f"Execution order: **{' → '.join(preview['order'])}**",
        "",
        "## Exact configurations",
        "",
    ]
    for side in ("A", "B"):
        config = preview["payloads"][side]["configuration"]
        lines.append(
            f"- **{side}**: `{config['provider']} / {config['model_id']} / "
            f"{config['effort']} / {config['execution_mode']}`"
        )
    lines.extend(
        [
            "",
            (
                "Run again with `--apply --runner ...` only after confirming that the adapter "
                "can honor every configuration field shown above. The adapter must echo the "
                "actual applied configuration in its result."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_or_print(path: str | None, text: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text)


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
            "Preview or execute one saved AI Model Advisor A/B experiment. "
            "Execution is opt-in with --apply."
        )
    )
    parser.add_argument("--plan", required=True, help="JSON produced by experiment-plan")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--feedback", required=True, help="Advisor feedback JSONL")
    task_group = parser.add_mutually_exclusive_group(required=True)
    task_group.add_argument("--task", help="Fixed benchmark task text")
    task_group.add_argument("--task-file", help="UTF-8 file containing the fixed benchmark task")
    parser.add_argument("--task-id", help="Optional exact task ID from the saved experiment prefix")
    parser.add_argument("--order", choices=["auto", "ab", "ba"], default="auto")
    parser.add_argument("--allow-ready", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually launch both adapter runs and append the validated pair to feedback",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="Per-side adapter timeout; only used with --apply",
    )
    parser.add_argument("--output", help="Optional Markdown report")
    parser.add_argument("--json-output")
    parser.add_argument(
        "--runner",
        nargs=argparse.REMAINDER,
        help=(
            "Adapter executable argv. It receives one JSON object on stdin and must print a JSON "
            "result object as its last non-empty stdout line. Required only with --apply. "
            "Place --runner last so following tokens belong to the adapter."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = _load_json_object(args.plan)
    feedback = FeedbackStore.load(args.feedback)
    task = _task_text(args)

    preview = build_preview(
        plan,
        feedback,
        args.experiment_id,
        task,
        task_id=args.task_id,
        order=args.order,
        allow_ready=args.allow_ready,
    )
    if not args.apply:
        _write_or_print(args.output, preview_markdown(preview))
        _write_json(args.json_output, preview)
        return 0

    runner_argv = list(args.runner or [])
    if not runner_argv:
        raise ExperimentRunnerError("--runner is required when --apply is used")

    # Reload immediately before spending provider credits so a previous run or
    # another writer that completed after the initial preview is noticed.
    live_feedback = FeedbackStore.load(args.feedback)
    build_preview(
        plan,
        live_feedback,
        args.experiment_id,
        task,
        task_id=preview["task_id"],
        order=args.order,
        allow_ready=args.allow_ready,
    )
    report = run_experiment_pair(
        plan,
        live_feedback,
        args.experiment_id,
        task,
        command_executor(runner_argv, args.timeout_seconds),
        task_id=preview["task_id"],
        order=args.order,
        allow_ready=args.allow_ready,
    )
    append_pair_feedback(args.feedback, report)
    _write_or_print(args.output, experiment_run_markdown(report, applied=True))
    _write_json(args.json_output, pair_run_json(report, applied=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
