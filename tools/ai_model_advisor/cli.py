from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .activity import ActivityAnalyzer
from .feedback import FeedbackStore, UsageRecord
from .hive_trace import (
    append_imported_feedback,
    import_hive_trace,
    import_report_markdown,
)
from .matrix import build_routing_matrix, routing_matrix_markdown
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .report import recommendation_markdown
from .sources import (
    baseline_from_report,
    load_baseline,
    scan_markdown,
    scan_official_sources,
)


def _write(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _profile_to_json(profile) -> str:
    return json.dumps(profile.as_dict(), ensure_ascii=False, indent=2) + "\n"


def _activity_texts(args: argparse.Namespace, analyzer: ActivityAnalyzer) -> list[str]:
    if args.chatgpt_export:
        return analyzer.texts_from_chatgpt_export(args.chatgpt_export)
    if args.github_user:
        events = analyzer.fetch_github_public_events(args.github_user, os.getenv("GITHUB_TOKEN"))
        return analyzer.texts_from_github_events(events)
    if args.input:
        return analyzer.texts_from_generic_json(args.input)
    raise SystemExit("Provide --input, --chatgpt-export, or --github-user")


def command_profile(args: argparse.Namespace) -> int:
    analyzer = ActivityAnalyzer()
    profile = analyzer.from_texts(_activity_texts(args, analyzer))
    _write(args.output, _profile_to_json(profile))
    return 0


def _load_profile(path: str | Path):
    from .models import WorkloadProfile

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = set(WorkloadProfile.__dataclass_fields__)
    return WorkloadProfile(**{key: value for key, value in data.items() if key in allowed})


def command_recommend(args: argparse.Namespace) -> int:
    registry = ModelRegistry(args.registry)
    profile = _load_profile(args.profile)
    if args.cost_sensitivity is not None:
        profile.cost_sensitivity = args.cost_sensitivity
    if args.latency_sensitivity is not None:
        profile.latency_sensitivity = args.latency_sensitivity
    feedback = FeedbackStore.load(args.feedback)
    recommendations = RecommendationEngine(registry, feedback).recommend(
        profile,
        providers=args.provider or None,
        include_limited=args.include_limited,
        top_n=args.top,
    )
    _write(args.output, recommendation_markdown(profile, recommendations, registry.as_of))
    if args.json_output:
        payload = {
            "registry_as_of": registry.as_of,
            "workload": profile.as_dict(),
            "recommendations": [item.as_dict() for item in recommendations],
        }
        _write(args.json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_matrix(args: argparse.Namespace) -> int:
    analyzer = ActivityAnalyzer()
    profiles = analyzer.category_profiles_from_texts(_activity_texts(args, analyzer))
    registry = ModelRegistry(args.registry)
    feedback = FeedbackStore.load(args.feedback)
    engine = RecommendationEngine(registry, feedback)
    rows = build_routing_matrix(
        profiles,
        engine,
        providers=args.provider or None,
        include_limited=args.include_limited,
        alternatives=args.alternatives,
    )
    _write(args.output, routing_matrix_markdown(rows, registry.as_of))
    if args.json_output:
        payload = {"registry_as_of": registry.as_of, "routing_matrix": rows}
        _write(args.json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_feedback_add(args: argparse.Namespace) -> int:
    record = UsageRecord(
        provider=args.provider,
        model_id=args.model,
        effort=args.effort,
        execution_mode=args.execution,
        outcome=args.outcome,
        retries=args.retries,
        latency_seconds=args.latency_seconds,
        cost_usd=args.cost_usd,
        task_category=args.task_category,
        task_id=args.task_id,
        note=args.note or "",
    )
    FeedbackStore.append(args.feedback, record)
    return 0


def command_feedback_import_hive(args: argparse.Namespace) -> int:
    registry = ModelRegistry(args.registry)
    report = import_hive_trace(
        events_path=args.events,
        details_path=args.details,
        registry=registry,
        task_category=args.task_category,
        effort=args.effort,
        execution_mode=args.execution,
        node_id=args.node_id,
        task_id=args.task_id,
    )
    append_result = None
    if not args.dry_run:
        append_result = append_imported_feedback(args.feedback, report.records)

    markdown = import_report_markdown(report, append_result)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        payload = report.as_dict()
        payload["append"] = asdict(append_result) if append_result else None
        payload["dry_run"] = bool(args.dry_run)
        _write(args.json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_scan(args: argparse.Namespace) -> int:
    registry = ModelRegistry(args.registry)
    previous_baseline = load_baseline(args.baseline)
    report = scan_official_sources(registry, args.baseline)
    _write(args.output, scan_markdown(report))
    if args.json_output:
        _write(args.json_output, json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n")
    if args.write_baseline:
        baseline = baseline_from_report(report, previous_baseline)
        _write(args.write_baseline, json.dumps(baseline, indent=2) + "\n")
    return 0


def _add_activity_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input")
    parser.add_argument("--chatgpt-export")
    parser.add_argument("--github-user")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track AI model changes and recommend model/mode by workload"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile")
    _add_activity_source_arguments(profile)
    profile.add_argument("--output", required=True)
    profile.set_defaults(func=command_profile)

    recommend = sub.add_parser("recommend")
    recommend.add_argument("--profile", required=True)
    recommend.add_argument("--registry")
    recommend.add_argument("--provider", action="append", choices=["openai", "anthropic"])
    recommend.add_argument("--include-limited", action="store_true")
    recommend.add_argument("--cost-sensitivity", type=float, choices=[1, 2, 3, 4, 5])
    recommend.add_argument("--latency-sensitivity", type=float, choices=[1, 2, 3, 4, 5])
    recommend.add_argument("--feedback")
    recommend.add_argument("--top", type=int, default=3)
    recommend.add_argument("--output", required=True)
    recommend.add_argument("--json-output")
    recommend.set_defaults(func=command_recommend)

    matrix = sub.add_parser("matrix")
    _add_activity_source_arguments(matrix)
    matrix.add_argument("--registry")
    matrix.add_argument("--provider", action="append", choices=["openai", "anthropic"])
    matrix.add_argument("--include-limited", action="store_true")
    matrix.add_argument("--feedback")
    matrix.add_argument("--alternatives", type=int, default=2, choices=[0, 1, 2, 3])
    matrix.add_argument("--output", required=True)
    matrix.add_argument("--json-output")
    matrix.set_defaults(func=command_matrix)

    feedback = sub.add_parser("feedback-add")
    feedback.add_argument("--feedback", required=True)
    feedback.add_argument("--provider", required=True, choices=["openai", "anthropic"])
    feedback.add_argument("--model", required=True)
    feedback.add_argument("--effort", required=True)
    feedback.add_argument("--execution", required=True)
    feedback.add_argument("--outcome", required=True, choices=["success", "partial", "failure"])
    feedback.add_argument("--retries", type=int, default=0)
    feedback.add_argument("--latency-seconds", type=float)
    feedback.add_argument("--cost-usd", type=float)
    feedback.add_argument("--task-category")
    feedback.add_argument(
        "--task-id",
        help="Stable ID shared by multiple model/config attempts of the same task",
    )
    feedback.add_argument("--note")
    feedback.set_defaults(func=command_feedback_add)

    hive_import = sub.add_parser("feedback-import-hive")
    hive_import.add_argument("--events", required=True, help="Hive session events.jsonl")
    hive_import.add_argument("--details", help="Hive session logs/details.jsonl")
    hive_import.add_argument("--feedback", required=True, help="Advisor feedback JSONL")
    hive_import.add_argument("--registry")
    hive_import.add_argument("--task-category")
    hive_import.add_argument("--effort", default="observed")
    hive_import.add_argument("--execution", default="hive_agent_loop")
    hive_import.add_argument("--node-id", help="Import only one node from the session")
    hive_import.add_argument(
        "--task-id",
        help="Stable benchmark task ID; requires --node-id",
    )
    hive_import.add_argument("--dry-run", action="store_true")
    hive_import.add_argument("--output", help="Optional Markdown import report")
    hive_import.add_argument("--json-output")
    hive_import.set_defaults(func=command_feedback_import_hive)

    scan = sub.add_parser("scan")
    scan.add_argument("--registry")
    scan.add_argument("--baseline")
    scan.add_argument("--output", required=True)
    scan.add_argument("--json-output")
    scan.add_argument("--write-baseline")
    scan.set_defaults(func=command_scan)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
