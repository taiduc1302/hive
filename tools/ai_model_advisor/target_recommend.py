from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path

from .execution_targets import profile_for_host, target_catalog
from .feedback import FeedbackStore
from .models import WorkloadProfile
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .report import recommendation_markdown
from .target_routing import recommend_for_target


def _load_workload(path: str | Path) -> WorkloadProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("profile JSON must be an object")
    allowed = {item.name for item in fields(WorkloadProfile)}
    return WorkloadProfile(**{key: value for key, value in payload.items() if key in allowed})


def _write(path: str | Path | None, text: str) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate recommendations constrained to an executable Advisor target.")
    parser.add_argument("--profile", required=True, help="Workload profile JSON")
    parser.add_argument(
        "--target",
        required=True,
        choices=sorted(target_catalog()),
        help="Execution target capability contract",
    )
    parser.add_argument("--registry", help="Optional model registry JSON")
    parser.add_argument("--feedback", help="Optional empirical feedback JSONL")
    parser.add_argument(
        "--provider",
        action="append",
        choices=("openai", "anthropic"),
        help="Restrict provider; repeat to allow both",
    )
    parser.add_argument("--include-limited", action="store_true")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--output", help="Markdown output path; stdout when omitted")
    parser.add_argument("--json-output", help="Optional JSON output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top <= 0:
        raise SystemExit("--top must be > 0")

    registry = ModelRegistry(args.registry)
    workload = _load_workload(args.profile)
    engine = RecommendationEngine(registry, FeedbackStore.load(args.feedback))
    target = profile_for_host(args.target)
    recommendations = recommend_for_target(
        engine,
        workload,
        args.target,
        providers=args.provider,
        include_limited=args.include_limited,
        top_n=args.top,
    )

    header = "\n".join(
        [
            "# Target-Aware AI Model Recommendation",
            "",
            f"Execution target: **{target.host}**",
            f"Adapter: `{target.adapter}`",
            f"Executable modes: {', '.join(f'`{mode}`' for mode in target.execution_modes)}",
            "",
            "The ranking below was recomputed inside this target's executable configuration space.",
            "",
        ]
    )
    report = recommendation_markdown(workload, recommendations, registry.as_of)
    if report.startswith("# AI Model Advisor\n"):
        report = report[len("# AI Model Advisor\n") :].lstrip("\n")
    markdown = header + report
    _write(args.output, markdown)

    if args.json_output:
        payload = {
            "schema_version": 1,
            "registry_as_of": registry.as_of,
            "execution_target": target.as_dict(),
            "workload": workload.as_dict(),
            "recommendations": [item.as_dict() for item in recommendations],
        }
        target_path = Path(args.json_output)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
