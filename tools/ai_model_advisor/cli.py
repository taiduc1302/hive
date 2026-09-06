from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .activity import ActivityAnalyzer
from .feedback import FeedbackStore, UsageRecord
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


def command_profile(args: argparse.Namespace) -> int:
    analyzer = ActivityAnalyzer()
    if args.chatgpt_export:
        profile = analyzer.from_chatgpt_export(args.chatgpt_export)
    elif args.github_user:
        profile = analyzer.from_github_events(
            analyzer.fetch_github_public_events(args.github_user, os.getenv("GITHUB_TOKEN"))
        )
    elif args.input:
        profile = analyzer.from_generic_json(args.input)
    else:
        raise SystemExit("Provide --input, --chatgpt-export, or --github-user")
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
        note=args.note or "",
    )
    FeedbackStore.append(args.feedback, record)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track AI model changes and recommend model/mode by workload"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile")
    profile.add_argument("--input")
    profile.add_argument("--chatgpt-export")
    profile.add_argument("--github-user")
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
    feedback.add_argument("--note")
    feedback.set_defaults(func=command_feedback_add)

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
