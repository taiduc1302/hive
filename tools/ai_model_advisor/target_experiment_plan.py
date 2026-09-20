from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .activity import ActivityAnalyzer
from .execution_targets import configuration_blockers, profile_for_host, target_catalog
from .experiment_plan import (
    _model_profile,
    _pair,
    experiment_plan_markdown,
)
from .experiment_target import bind_execution_target
from .feedback import EXACT_FEEDBACK_MIN, PAIRED_EFFICIENCY_MIN, FeedbackStore
from .models import ModelProfile, Recommendation, WorkloadProfile
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .target_routing import recommend_for_target


def _target_configurations(
    engine: RecommendationEngine,
    model: ModelProfile,
    workload: WorkloadProfile,
    category: str,
    execution_target: str,
) -> list[Recommendation]:
    profile = profile_for_host(execution_target)
    allowed_modes = tuple(
        mode
        for mode in model.execution_modes
        if not configuration_blockers(
            {
                "provider": model.provider,
                "model_id": model.model_id,
                "effort": model.default_effort or "default",
                "execution_mode": mode,
            },
            profile,
        )
    )
    if not allowed_modes:
        return []

    restricted_model = replace(model, execution_modes=allowed_modes)
    preferred_effort = engine._effort(model, workload)
    preferred_mode = engine._execution_mode(restricted_model, workload)
    rows = [
        engine._score_configuration(
            model,
            workload,
            effort,
            mode,
            preferred_effort,
            preferred_mode,
            category,
        )
        for effort in model.efforts
        for mode in allowed_modes
        if not configuration_blockers(
            {
                "provider": model.provider,
                "model_id": model.model_id,
                "effort": effort,
                "execution_mode": mode,
            },
            profile,
        )
    ]
    rows.sort(
        key=lambda item: (
            item.score,
            item.configuration_adjustment,
            item.effort == preferred_effort,
            item.execution_mode == preferred_mode,
        ),
        reverse=True,
    )
    return rows


def build_target_experiment_plan(
    profiles: dict[str, WorkloadProfile],
    engine: RecommendationEngine,
    execution_target: str,
    providers: Iterable[str] | None = None,
    include_limited: bool = False,
) -> dict[str, Any]:
    """Build and bind A/B pairs that the selected trusted target can execute."""

    target = profile_for_host(execution_target)
    categories: list[dict[str, Any]] = []
    ordered = sorted(
        profiles.items(),
        key=lambda item: (-item[1].activity_count, item[0]),
    )

    for category, workload in ordered:
        recommendations = recommend_for_target(
            engine,
            workload,
            execution_target,
            providers=providers,
            include_limited=include_limited,
            top_n=2,
        )
        if not recommendations:
            continue
        primary = recommendations[0]
        pairs: list[dict[str, Any]] = []

        if len(recommendations) > 1:
            pairs.append(
                _pair(
                    category,
                    "model",
                    primary,
                    recommendations[1],
                    engine.feedback,
                    priority=1,
                )
            )

        model = _model_profile(engine, primary.model_id)
        if model is not None:
            same_model = _target_configurations(
                engine,
                model,
                workload,
                category,
                execution_target,
            )
            effort_challenger = next(
                (candidate for candidate in same_model if candidate.execution_mode == primary.execution_mode and candidate.effort != primary.effort),
                None,
            )
            if effort_challenger is not None:
                pairs.append(
                    _pair(
                        category,
                        "effort",
                        primary,
                        effort_challenger,
                        engine.feedback,
                        priority=2,
                    )
                )

            execution_challenger = next(
                (candidate for candidate in same_model if candidate.effort == primary.effort and candidate.execution_mode != primary.execution_mode),
                None,
            )
            if execution_challenger is not None:
                pairs.append(
                    _pair(
                        category,
                        "execution",
                        primary,
                        execution_challenger,
                        engine.feedback,
                        priority=3,
                    )
                )

        categories.append(
            {
                "category": category,
                "activity_count": workload.activity_count,
                "workload": workload.as_dict(),
                "primary": primary.as_dict(),
                "pairs": pairs,
            }
        )

    all_pairs = [pair for item in categories for pair in item["pairs"]]
    provider_scope = list(providers) if providers else list(target.supported_providers)
    plan = {
        "routing_scope": {
            "providers": provider_scope,
            "include_limited": bool(include_limited),
            "execution_target": execution_target,
        },
        "paired_quality_threshold": EXACT_FEEDBACK_MIN,
        "paired_efficiency_threshold": PAIRED_EFFICIENCY_MIN,
        "categories": categories,
        "experiments": len(all_pairs),
        "planned_experiments": sum(pair["status"] == "planned" for pair in all_pairs),
        "collecting_experiments": sum(pair["status"] == "collecting" for pair in all_pairs),
        "ready_experiments": sum(pair["status"] == "ready" for pair in all_pairs),
    }
    return bind_execution_target(plan, execution_target)


def _activity_texts(args: argparse.Namespace, analyzer: ActivityAnalyzer) -> list[str]:
    if args.chatgpt_export:
        return analyzer.texts_from_chatgpt_export(args.chatgpt_export)
    if args.github_user:
        events = analyzer.fetch_github_public_events(args.github_user, os.getenv("GITHUB_TOKEN"))
        return analyzer.texts_from_github_events(events)
    if args.input:
        return analyzer.texts_from_generic_json(args.input)
    raise SystemExit("Provide --input, --chatgpt-export, or --github-user")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an experiment plan inside a trusted execution target's executable space.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input")
    source.add_argument("--chatgpt-export")
    source.add_argument("--github-user")
    parser.add_argument("--target", required=True, choices=sorted(target_catalog()))
    parser.add_argument("--registry")
    parser.add_argument("--feedback")
    parser.add_argument("--provider", action="append", choices=["openai", "anthropic"])
    parser.add_argument("--include-limited", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--json-output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    analyzer = ActivityAnalyzer()
    profiles = analyzer.category_profiles_from_texts(_activity_texts(args, analyzer))
    registry = ModelRegistry(args.registry)
    feedback = FeedbackStore.load(args.feedback)
    plan = build_target_experiment_plan(
        profiles,
        RecommendationEngine(registry, feedback),
        args.target,
        providers=args.provider or None,
        include_limited=args.include_limited,
    )

    markdown = experiment_plan_markdown(plan)
    target = profile_for_host(args.target)
    prefix = "\n".join(
        [
            "# Target-Aware AI Model Advisor Experiment Plan",
            "",
            f"Execution target: **{target.host}** (`{target.adapter}`)",
            ("All proposed configurations were ranked inside this target's trusted execution contract before the plan was bound."),
            "",
        ]
    )
    if markdown.startswith("# AI Model Advisor Experiment Plan\n"):
        markdown = markdown[len("# AI Model Advisor Experiment Plan\n") :].lstrip("\n")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(prefix + markdown, encoding="utf-8")
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_output).write_text(
        json.dumps({"registry_as_of": registry.as_of, **plan}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
