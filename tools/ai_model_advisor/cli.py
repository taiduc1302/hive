from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .activity import ActivityAnalyzer
from .empirical_leaderboard import build_empirical_leaderboard, empirical_leaderboard_markdown
from .experiment_evaluate import evaluate_experiment_plan, experiment_evaluation_markdown
from .experiment_impact import build_experiment_impact, experiment_impact_markdown
from .experiment_plan import build_experiment_plan, experiment_plan_markdown
from .experiment_run_cli import main as experiment_run_main
from .feedback import FeedbackStore, UsageRecord
from .feedback_report import build_feedback_audit, feedback_audit_markdown
from .hive_history import history_import_markdown, import_hive_history
from .hive_promotion_gate import (
    build_hive_promotion_gate,
    render_markdown as hive_promotion_gate_markdown,
)
from .hive_promotion_journal import (
    append_hive_promotion_journal,
    build_hive_promotion_journal,
    render_markdown as hive_promotion_journal_markdown,
)
from .hive_promotion_lifecycle import (
    build_hive_promotion_lifecycle,
    render_markdown as hive_promotion_lifecycle_markdown,
)
from .hive_promotion_preview import (
    build_hive_promotion_preview,
    render_markdown as hive_promotion_preview_markdown,
)
from .hive_promotion_receipt import (
    build_hive_promotion_receipt,
    render_markdown as hive_promotion_receipt_markdown,
)
from .hive_promotion_reconcile import (
    build_hive_promotion_reconciliation,
    render_markdown as hive_promotion_reconcile_markdown,
)
from .hive_promotion_registry import (
    build_hive_promotion_registry,
    render_markdown as hive_promotion_registry_markdown,
)
from .hive_promotion_rollback import (
    build_hive_promotion_rollback_audit,
    render_markdown as hive_promotion_rollback_markdown,
)
from .hive_promotion_rollback_finalize import (
    build_hive_promotion_rollback_finalization,
    render_markdown as hive_promotion_rollback_finalize_markdown,
)
from .hive_promotion_rollback_plan import (
    build_hive_promotion_rollback_plan,
    render_markdown as hive_promotion_rollback_plan_markdown,
)
from .hive_promotion_rollback_preflight import (
    build_hive_promotion_rollback_preflight,
    render_markdown as hive_promotion_rollback_preflight_markdown,
)
from .hive_promotion_status import (
    build_hive_promotion_status,
    render_markdown as hive_promotion_status_markdown,
)
from .hive_trace import (
    append_imported_feedback,
    import_hive_trace,
    import_report_markdown,
)
from .matrix import build_routing_matrix, routing_matrix_markdown
from .promotion_plan import build_promotion_plans, promotion_plans_markdown
from .promotion_review import build_promotion_review, promotion_review_markdown
from .readiness import build_experiment_readiness, experiment_readiness_markdown
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .report import recommendation_markdown
from .routing_proposals import build_routing_proposals, routing_proposals_markdown
from .runtime_capabilities import build_capability_report
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


def command_experiment_plan(args: argparse.Namespace) -> int:
    analyzer = ActivityAnalyzer()
    profiles = analyzer.category_profiles_from_texts(_activity_texts(args, analyzer))
    registry = ModelRegistry(args.registry)
    feedback = FeedbackStore.load(args.feedback)
    plan = build_experiment_plan(
        profiles,
        RecommendationEngine(registry, feedback),
        providers=args.provider or None,
        include_limited=args.include_limited,
    )
    _write(args.output, experiment_plan_markdown(plan))
    if args.json_output:
        payload = {"registry_as_of": registry.as_of, **plan}
        _write(args.json_output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_experiment_run(args: argparse.Namespace) -> int:
    forwarded = [
        "--plan",
        args.plan,
        "--experiment-id",
        args.experiment_id,
        "--feedback",
        args.feedback,
        "--order",
        args.order,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--judge-timeout-seconds",
        str(args.judge_timeout_seconds),
    ]
    if args.task is not None:
        forwarded.extend(["--task", args.task])
    if args.task_file is not None:
        forwarded.extend(["--task-file", args.task_file])
    if args.task_id:
        forwarded.extend(["--task-id", args.task_id])
    if args.allow_ready:
        forwarded.append("--allow-ready")
    if args.apply:
        forwarded.append("--apply")
    if args.output:
        forwarded.extend(["--output", args.output])
    if args.json_output:
        forwarded.extend(["--json-output", args.json_output])
    if args.judge:
        forwarded.extend(["--judge", *args.judge])
    if args.runner:
        forwarded.extend(["--runner", *args.runner])
    return experiment_run_main(forwarded)


def command_experiment_evaluate(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Experiment plan root must be a JSON object")
    report = evaluate_experiment_plan(plan, FeedbackStore.load(args.feedback))
    _write(args.output, experiment_evaluation_markdown(report))
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_experiment_impact(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Experiment plan root must be a JSON object")
    registry = ModelRegistry(args.registry)
    feedback = FeedbackStore.load(args.feedback)
    report = build_experiment_impact(
        plan,
        feedback,
        registry,
        providers=args.provider or None,
        include_limited=args.include_limited,
    )
    _write(args.output, experiment_impact_markdown(report))
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
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
        input_tokens=args.input_tokens,
        output_tokens=args.output_tokens,
        cached_tokens=args.cached_tokens,
        cache_creation_tokens=args.cache_creation_tokens,
        credits=args.credits,
        task_category=args.task_category,
        task_id=args.task_id,
        note=args.note or "",
    )
    FeedbackStore.append(args.feedback, record)
    return 0


def command_feedback_report(args: argparse.Namespace) -> int:
    audit = build_feedback_audit(FeedbackStore.load(args.feedback))
    markdown = feedback_audit_markdown(audit)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_feedback_readiness(args: argparse.Namespace) -> int:
    report = build_experiment_readiness(FeedbackStore.load(args.feedback))
    markdown = experiment_readiness_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_feedback_leaderboard(args: argparse.Namespace) -> int:
    report = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    markdown = empirical_leaderboard_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def _routing_matrix_rows(path: str | Path) -> list[dict[str, object]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("routing_matrix", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("routing matrix JSON must be a list or contain a routing_matrix list")
    return rows


def command_routing_proposals(args: argparse.Namespace) -> int:
    rows = _routing_matrix_rows(args.routing_matrix)
    leaderboard = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    report = build_routing_proposals(rows, leaderboard)
    markdown = routing_proposals_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_promotion_plan(args: argparse.Namespace) -> int:
    rows = _routing_matrix_rows(args.routing_matrix)
    leaderboard = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    proposals = build_routing_proposals(rows, leaderboard)
    report = build_promotion_plans(proposals, leaderboard)
    markdown = promotion_plans_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_promotion_review(args: argparse.Namespace) -> int:
    canary_report = json.loads(Path(args.canary_evaluation).read_text(encoding="utf-8"))
    if not isinstance(canary_report, dict):
        raise ValueError("canary evaluation root must be a JSON object")
    report = build_promotion_review(
        canary_report,
        _routing_matrix_rows(args.routing_matrix),
    )
    markdown = promotion_review_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    return 0


def command_hive_promotion_preview(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.promotion_review).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("promotion review root must be a JSON object")
    preview = build_hive_promotion_preview(
        report,
        category=args.category,
        scope=args.scope,
    )
    markdown = hive_promotion_preview_markdown(preview)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(preview, ensure_ascii=False, indent=2) + "\n",
        )
    return 0


def command_hive_promotion_gate(args: argparse.Namespace) -> int:
    preview = json.loads(Path(args.promotion_preview).read_text(encoding="utf-8"))
    if not isinstance(preview, dict):
        raise ValueError("promotion preview root must be a JSON object")

    if args.runtime_capabilities:
        capabilities = json.loads(
            Path(args.runtime_capabilities).read_text(encoding="utf-8")
        )
        if not isinstance(capabilities, dict):
            raise ValueError("runtime capabilities root must be a JSON object")
    else:
        capabilities = build_capability_report()

    evidence = None
    if args.hive_evidence:
        evidence = json.loads(Path(args.hive_evidence).read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ValueError("Hive evidence root must be a JSON object")

    gate = build_hive_promotion_gate(preview, capabilities, evidence)
    markdown = hive_promotion_gate_markdown(gate)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(gate, ensure_ascii=False, indent=2) + "\n",
        )
    if args.require_ready and not gate.get("ready"):
        return 2
    return 0


def command_hive_promotion_lifecycle(args: argparse.Namespace) -> int:
    review = json.loads(Path(args.promotion_review).read_text(encoding="utf-8"))
    if not isinstance(review, dict):
        raise ValueError("promotion review root must be a JSON object")
    preview = json.loads(Path(args.promotion_preview).read_text(encoding="utf-8"))
    if not isinstance(preview, dict):
        raise ValueError("promotion preview root must be a JSON object")

    runtime_gate = None
    if args.runtime_gate:
        runtime_gate = json.loads(Path(args.runtime_gate).read_text(encoding="utf-8"))
        if not isinstance(runtime_gate, dict):
            raise ValueError("runtime gate root must be a JSON object")

    promotion_receipt = None
    if args.promotion_receipt:
        promotion_receipt = json.loads(
            Path(args.promotion_receipt).read_text(encoding="utf-8")
        )
        if not isinstance(promotion_receipt, dict):
            raise ValueError("promotion receipt root must be a JSON object")

    report = build_hive_promotion_lifecycle(
        review,
        preview,
        runtime_gate,
        promotion_receipt,
    )
    markdown = hive_promotion_lifecycle_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    if args.require_applied_verified and not report.get("applied_verified"):
        return 2
    return 0


def command_hive_promotion_journal(args: argparse.Namespace) -> int:
    if args.journal_action == "init":
        preview = json.loads(
            Path(args.promotion_preview).read_text(encoding="utf-8")
        )
        if not isinstance(preview, dict):
            raise ValueError("promotion preview root must be a JSON object")
        journal = build_hive_promotion_journal(preview)
    else:
        current = json.loads(Path(args.journal).read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("promotion journal root must be a JSON object")
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise ValueError("journal event artifact root must be a JSON object")
        journal = append_hive_promotion_journal(
            current,
            event=args.event,
            artifact=artifact,
        )

    markdown = hive_promotion_journal_markdown(journal)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        )
    return 0


def command_hive_promotion_registry(args: argparse.Namespace) -> int:
    snapshots = []
    for journal_path, checkpoint_path in args.pair:
        journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
        checkpoint = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
        if not isinstance(journal, dict):
            raise ValueError("promotion journal root must be a JSON object")
        if not isinstance(checkpoint, dict):
            raise ValueError("promotion checkpoint root must be a JSON object")
        snapshots.append((journal, checkpoint))

    registry = build_hive_promotion_registry(snapshots)
    markdown = hive_promotion_registry_markdown(registry)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        )
    if args.require_ready and registry.get("status") != "ready":
        return 2
    return 0


def command_hive_promotion_reconcile(args: argparse.Namespace) -> int:
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    config = json.loads(Path(args.hive_config).read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise ValueError("promotion registry root must be a JSON object")
    if not isinstance(config, dict):
        raise ValueError("Hive configuration root must be a JSON object")
    report = build_hive_promotion_reconciliation(registry, config)
    markdown = hive_promotion_reconcile_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.require_verified and not report.get("verified"):
        return 2
    return 0


def command_hive_promotion_rollback_plan(args: argparse.Namespace) -> int:
    status = json.loads(Path(args.status).read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        raise ValueError("promotion status root must be a JSON object")
    plan = build_hive_promotion_rollback_plan(status)
    markdown = hive_promotion_rollback_plan_markdown(plan)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    if args.require_ready and not plan.get("ready_for_manual_rollback"):
        return 2
    return 0


def command_hive_promotion_rollback_preflight(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.rollback_plan).read_text(encoding="utf-8"))
    config = json.loads(Path(args.hive_config).read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("rollback plan root must be a JSON object")
    if not isinstance(config, dict):
        raise ValueError("Hive configuration root must be a JSON object")
    report = build_hive_promotion_rollback_preflight(plan, config)
    markdown = hive_promotion_rollback_preflight_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.require_ready and not report.get("ready_for_manual_edit"):
        return 2
    return 0


def command_hive_promotion_status(args: argparse.Namespace) -> int:
    snapshots = []
    for journal_path, checkpoint_path in args.pair:
        journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
        checkpoint = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
        if not isinstance(journal, dict):
            raise ValueError("promotion journal root must be a JSON object")
        if not isinstance(checkpoint, dict):
            raise ValueError("promotion checkpoint root must be a JSON object")
        snapshots.append((journal, checkpoint))
    config = json.loads(Path(args.hive_config).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Hive configuration root must be a JSON object")

    report = build_hive_promotion_status(snapshots, config)
    markdown = hive_promotion_status_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(args.json_output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.require_verified and report.get("status") != "verified":
        return 2
    return 0


def command_hive_promotion_rollback(args: argparse.Namespace) -> int:
    preview = json.loads(Path(args.promotion_preview).read_text(encoding="utf-8"))
    if not isinstance(preview, dict):
        raise ValueError("promotion preview root must be a JSON object")
    lifecycle = json.loads(Path(args.applied_lifecycle).read_text(encoding="utf-8"))
    if not isinstance(lifecycle, dict):
        raise ValueError("applied lifecycle root must be a JSON object")
    receipt = json.loads(Path(args.rollback_receipt).read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("rollback receipt root must be a JSON object")

    report = build_hive_promotion_rollback_audit(
        preview,
        lifecycle,
        receipt,
    )
    markdown = hive_promotion_rollback_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    if args.require_rolled_back_verified and not report.get(
        "rolled_back_verified"
    ):
        return 2
    return 0


def command_hive_promotion_rollback_finalize(args: argparse.Namespace) -> int:
    preview = json.loads(Path(args.promotion_preview).read_text(encoding="utf-8"))
    lifecycle = json.loads(Path(args.applied_lifecycle).read_text(encoding="utf-8"))
    plan = json.loads(Path(args.rollback_plan).read_text(encoding="utf-8"))
    preflight = json.loads(Path(args.rollback_preflight).read_text(encoding="utf-8"))
    receipt = json.loads(Path(args.rollback_receipt).read_text(encoding="utf-8"))
    for label, payload in (
        ("promotion preview", preview),
        ("applied lifecycle", lifecycle),
        ("rollback plan", plan),
        ("rollback preflight", preflight),
        ("rollback receipt", receipt),
    ):
        if not isinstance(payload, dict):
            raise ValueError(f"{label} root must be a JSON object")

    report = build_hive_promotion_rollback_finalization(
        preview,
        lifecycle,
        plan,
        preflight,
        receipt,
    )
    markdown = hive_promotion_rollback_finalize_markdown(report)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    if args.require_verified and not report.get(
        "rollback_verified_from_fresh_preflight"
    ):
        return 2
    return 0


def command_hive_promotion_receipt(args: argparse.Namespace) -> int:
    preview = json.loads(Path(args.promotion_preview).read_text(encoding="utf-8"))
    if not isinstance(preview, dict):
        raise ValueError("promotion preview root must be a JSON object")
    current_config = json.loads(Path(args.hive_config).read_text(encoding="utf-8"))
    if not isinstance(current_config, dict):
        raise ValueError("Hive configuration root must be a JSON object")
    previous_receipt = None
    previous_receipt_path = getattr(args, "previous_receipt", None)
    if previous_receipt_path:
        previous_receipt = json.loads(
            Path(previous_receipt_path).read_text(encoding="utf-8")
        )
        if not isinstance(previous_receipt, dict):
            raise ValueError("previous receipt root must be a JSON object")
    receipt = build_hive_promotion_receipt(
        preview,
        current_config,
        previous_receipt=previous_receipt,
    )
    markdown = hive_promotion_receipt_markdown(receipt)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        )
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


def command_feedback_import_hive_root(args: argparse.Namespace) -> int:
    registry = ModelRegistry(args.registry)
    report = import_hive_history(
        args.root,
        registry,
        task_category=args.task_category,
        effort=args.effort,
        execution_mode=args.execution,
    )
    append_result = None
    if args.apply:
        append_result = append_imported_feedback(args.feedback, report.records)

    markdown = history_import_markdown(report, append_result, applied=args.apply)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        payload = report.as_dict()
        payload["append"] = asdict(append_result) if append_result else None
        payload["applied"] = bool(args.apply)
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
    parser = argparse.ArgumentParser(description="Track AI model changes and recommend model/mode by workload")
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

    experiment_plan = sub.add_parser("experiment-plan")
    _add_activity_source_arguments(experiment_plan)
    experiment_plan.add_argument("--registry")
    experiment_plan.add_argument("--provider", action="append", choices=["openai", "anthropic"])
    experiment_plan.add_argument("--include-limited", action="store_true")
    experiment_plan.add_argument("--feedback")
    experiment_plan.add_argument("--output", required=True)
    experiment_plan.add_argument("--json-output")
    experiment_plan.set_defaults(func=command_experiment_plan)

    experiment_run = sub.add_parser("experiment-run")
    experiment_run.add_argument("--plan", required=True, help="JSON from experiment-plan")
    experiment_run.add_argument("--experiment-id", required=True)
    experiment_run.add_argument("--feedback", required=True)
    task_group = experiment_run.add_mutually_exclusive_group(required=True)
    task_group.add_argument("--task")
    task_group.add_argument("--task-file")
    experiment_run.add_argument("--task-id")
    experiment_run.add_argument("--order", choices=["auto", "ab", "ba"], default="auto")
    experiment_run.add_argument("--allow-ready", action="store_true")
    experiment_run.add_argument("--apply", action="store_true")
    experiment_run.add_argument("--timeout-seconds", type=float, default=1800.0)
    experiment_run.add_argument("--judge-timeout-seconds", type=float, default=300.0)
    experiment_run.add_argument("--output")
    experiment_run.add_argument("--json-output")
    experiment_run.add_argument(
        "--judge",
        nargs="+",
        help="Optional deterministic outcome-checker argv; place before --runner.",
    )
    experiment_run.add_argument(
        "--runner",
        nargs=argparse.REMAINDER,
        help="Adapter argv; place --runner last. Required only with --apply.",
    )
    experiment_run.set_defaults(func=command_experiment_run)

    experiment_evaluate = sub.add_parser("experiment-evaluate")
    experiment_evaluate.add_argument("--plan", required=True, help="JSON from experiment-plan")
    experiment_evaluate.add_argument("--feedback", required=True)
    experiment_evaluate.add_argument("--output", required=True)
    experiment_evaluate.add_argument("--json-output")
    experiment_evaluate.set_defaults(func=command_experiment_evaluate)

    experiment_impact = sub.add_parser("experiment-impact")
    experiment_impact.add_argument("--plan", required=True, help="JSON from experiment-plan")
    experiment_impact.add_argument("--feedback", required=True)
    experiment_impact.add_argument("--registry")
    experiment_impact.add_argument("--provider", action="append", choices=["openai", "anthropic"])
    experiment_impact.add_argument("--include-limited", action="store_true")
    experiment_impact.add_argument("--output", required=True)
    experiment_impact.add_argument("--json-output")
    experiment_impact.set_defaults(func=command_experiment_impact)

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
    feedback.add_argument("--input-tokens", type=int)
    feedback.add_argument("--output-tokens", type=int)
    feedback.add_argument("--cached-tokens", type=int)
    feedback.add_argument("--cache-creation-tokens", type=int)
    feedback.add_argument("--credits", type=float)
    feedback.add_argument("--task-category")
    feedback.add_argument(
        "--task-id",
        help="Stable ID shared by multiple model/config attempts of the same task",
    )
    feedback.add_argument("--note")
    feedback.set_defaults(func=command_feedback_add)

    feedback_report = sub.add_parser("feedback-report")
    feedback_report.add_argument("--feedback", required=True)
    feedback_report.add_argument("--output", help="Optional Markdown evidence report")
    feedback_report.add_argument("--json-output")
    feedback_report.set_defaults(func=command_feedback_report)

    feedback_readiness = sub.add_parser("feedback-readiness")
    feedback_readiness.add_argument("--feedback", required=True)
    feedback_readiness.add_argument("--output", help="Optional Markdown experiment-readiness plan")
    feedback_readiness.add_argument("--json-output")
    feedback_readiness.set_defaults(func=command_feedback_readiness)

    feedback_leaderboard = sub.add_parser("feedback-leaderboard")
    feedback_leaderboard.add_argument("--feedback", required=True)
    feedback_leaderboard.add_argument("--output", help="Optional Markdown empirical leaderboard")
    feedback_leaderboard.add_argument("--json-output")
    feedback_leaderboard.set_defaults(func=command_feedback_leaderboard)

    routing_proposals = sub.add_parser("routing-proposals")
    routing_proposals.add_argument("--routing-matrix", required=True)
    routing_proposals.add_argument("--feedback", required=True)
    routing_proposals.add_argument("--output", help="Optional Markdown routing proposal report")
    routing_proposals.add_argument("--json-output")
    routing_proposals.set_defaults(func=command_routing_proposals)

    promotion_plan = sub.add_parser("promotion-plan")
    promotion_plan.add_argument("--routing-matrix", required=True)
    promotion_plan.add_argument("--feedback", required=True)
    promotion_plan.add_argument("--output", help="Optional Markdown promotion/canary plan")
    promotion_plan.add_argument("--json-output")
    promotion_plan.set_defaults(func=command_promotion_plan)

    promotion_review = sub.add_parser("promotion-review")
    promotion_review.add_argument("--canary-evaluation", required=True)
    promotion_review.add_argument("--routing-matrix", required=True)
    promotion_review.add_argument("--output", help="Optional Markdown manual promotion review")
    promotion_review.add_argument("--json-output")
    promotion_review.set_defaults(func=command_promotion_review)

    hive_promotion_preview = sub.add_parser("hive-promotion-preview")
    hive_promotion_preview.add_argument("--promotion-review", required=True)
    hive_promotion_preview.add_argument("--category", required=True)
    hive_promotion_preview.add_argument("--scope", choices=["queen", "worker", "both"], default="queen")
    hive_promotion_preview.add_argument("--output", help="Optional Markdown Hive promotion preview")
    hive_promotion_preview.add_argument("--json-output")
    hive_promotion_preview.set_defaults(func=command_hive_promotion_preview)

    hive_promotion_gate = sub.add_parser("hive-promotion-gate")
    hive_promotion_gate.add_argument("--promotion-preview", required=True)
    hive_promotion_gate.add_argument("--runtime-capabilities")
    hive_promotion_gate.add_argument("--hive-evidence")
    hive_promotion_gate.add_argument(
        "--output",
        help="Optional Markdown Hive promotion runtime gate report",
    )
    hive_promotion_gate.add_argument("--json-output")
    hive_promotion_gate.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 unless exact current-runtime evidence makes the gate ready",
    )
    hive_promotion_gate.set_defaults(func=command_hive_promotion_gate)

    hive_promotion_lifecycle = sub.add_parser("hive-promotion-lifecycle")
    hive_promotion_lifecycle.add_argument("--promotion-review", required=True)
    hive_promotion_lifecycle.add_argument("--promotion-preview", required=True)
    hive_promotion_lifecycle.add_argument("--runtime-gate")
    hive_promotion_lifecycle.add_argument("--promotion-receipt")
    hive_promotion_lifecycle.add_argument(
        "--output",
        help="Optional Markdown Hive promotion lifecycle audit",
    )
    hive_promotion_lifecycle.add_argument("--json-output")
    hive_promotion_lifecycle.add_argument(
        "--require-applied-verified",
        action="store_true",
        help="Exit 2 unless the lifecycle reaches applied_verified",
    )
    hive_promotion_lifecycle.set_defaults(func=command_hive_promotion_lifecycle)

    hive_promotion_journal = sub.add_parser("hive-promotion-journal")
    journal_sub = hive_promotion_journal.add_subparsers(
        dest="journal_action",
        required=True,
    )

    journal_init = journal_sub.add_parser("init")
    journal_init.add_argument("--promotion-preview", required=True)
    journal_init.add_argument("--output")
    journal_init.add_argument("--json-output")
    journal_init.set_defaults(func=command_hive_promotion_journal)

    journal_append = journal_sub.add_parser("append")
    journal_append.add_argument("--journal", required=True)
    journal_append.add_argument(
        "--event",
        choices=["applied_lifecycle", "rollback_audit"],
        required=True,
    )
    journal_append.add_argument("--artifact", required=True)
    journal_append.add_argument("--output")
    journal_append.add_argument("--json-output")
    journal_append.set_defaults(func=command_hive_promotion_journal)

    hive_promotion_registry = sub.add_parser("hive-promotion-registry")
    hive_promotion_registry.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("JOURNAL", "CHECKPOINT"),
        required=True,
        help="Journal/checkpoint pair. Repeat for additional promotion snapshots.",
    )
    hive_promotion_registry.add_argument("--output")
    hive_promotion_registry.add_argument("--json-output")
    hive_promotion_registry.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 when any registry blocker is present.",
    )
    hive_promotion_registry.set_defaults(func=command_hive_promotion_registry)

    hive_promotion_reconcile = sub.add_parser("hive-promotion-reconcile")
    hive_promotion_reconcile.add_argument("--registry", required=True)
    hive_promotion_reconcile.add_argument("--hive-config", required=True)
    hive_promotion_reconcile.add_argument("--output")
    hive_promotion_reconcile.add_argument("--json-output")
    hive_promotion_reconcile.add_argument("--require-verified", action="store_true")
    hive_promotion_reconcile.set_defaults(func=command_hive_promotion_reconcile)

    hive_promotion_rollback_plan = sub.add_parser("hive-promotion-rollback-plan")
    hive_promotion_rollback_plan.add_argument("--status", required=True)
    hive_promotion_rollback_plan.add_argument("--output")
    hive_promotion_rollback_plan.add_argument("--json-output")
    hive_promotion_rollback_plan.add_argument("--require-ready", action="store_true")
    hive_promotion_rollback_plan.set_defaults(
        func=command_hive_promotion_rollback_plan
    )

    hive_promotion_rollback_preflight = sub.add_parser(
        "hive-promotion-rollback-preflight"
    )
    hive_promotion_rollback_preflight.add_argument("--rollback-plan", required=True)
    hive_promotion_rollback_preflight.add_argument("--hive-config", required=True)
    hive_promotion_rollback_preflight.add_argument("--output")
    hive_promotion_rollback_preflight.add_argument("--json-output")
    hive_promotion_rollback_preflight.add_argument(
        "--require-ready",
        action="store_true",
    )
    hive_promotion_rollback_preflight.set_defaults(
        func=command_hive_promotion_rollback_preflight
    )

    hive_promotion_status = sub.add_parser("hive-promotion-status")
    hive_promotion_status.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("JOURNAL", "CHECKPOINT"),
        required=True,
        help="Journal/checkpoint pair. Repeat for additional snapshots.",
    )
    hive_promotion_status.add_argument("--hive-config", required=True)
    hive_promotion_status.add_argument("--output")
    hive_promotion_status.add_argument("--json-output")
    hive_promotion_status.add_argument("--require-verified", action="store_true")
    hive_promotion_status.set_defaults(func=command_hive_promotion_status)

    hive_promotion_rollback = sub.add_parser("hive-promotion-rollback")
    hive_promotion_rollback.add_argument("--promotion-preview", required=True)
    hive_promotion_rollback.add_argument("--applied-lifecycle", required=True)
    hive_promotion_rollback.add_argument("--rollback-receipt", required=True)
    hive_promotion_rollback.add_argument(
        "--output",
        help="Optional Markdown Hive promotion rollback audit",
    )
    hive_promotion_rollback.add_argument("--json-output")
    hive_promotion_rollback.add_argument(
        "--require-rolled-back-verified",
        action="store_true",
        help="Exit 2 unless the rollback is verified after a prior applied lifecycle",
    )
    hive_promotion_rollback.set_defaults(func=command_hive_promotion_rollback)

    hive_promotion_rollback_finalize = sub.add_parser(
        "hive-promotion-rollback-finalize"
    )
    hive_promotion_rollback_finalize.add_argument("--promotion-preview", required=True)
    hive_promotion_rollback_finalize.add_argument("--applied-lifecycle", required=True)
    hive_promotion_rollback_finalize.add_argument("--rollback-plan", required=True)
    hive_promotion_rollback_finalize.add_argument("--rollback-preflight", required=True)
    hive_promotion_rollback_finalize.add_argument("--rollback-receipt", required=True)
    hive_promotion_rollback_finalize.add_argument("--output")
    hive_promotion_rollback_finalize.add_argument("--json-output")
    hive_promotion_rollback_finalize.add_argument(
        "--require-verified",
        action="store_true",
    )
    hive_promotion_rollback_finalize.set_defaults(
        func=command_hive_promotion_rollback_finalize
    )

    hive_promotion_receipt = sub.add_parser("hive-promotion-receipt")
    hive_promotion_receipt.add_argument("--promotion-preview", required=True)
    hive_promotion_receipt.add_argument("--hive-config", required=True)
    hive_promotion_receipt.add_argument(
        "--previous-receipt",
        help=(
            "Optional prior verification receipt to hash-link this observation; "
            "required for replay-safe rollback evidence"
        ),
    )
    hive_promotion_receipt.add_argument(
        "--output",
        help="Optional Markdown Hive promotion verification receipt",
    )
    hive_promotion_receipt.add_argument("--json-output")
    hive_promotion_receipt.set_defaults(func=command_hive_promotion_receipt)

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

    hive_root = sub.add_parser("feedback-import-hive-root")
    hive_root.add_argument("--root", required=True, help="Hive storage root to scan recursively")
    hive_root.add_argument("--feedback", required=True, help="Advisor feedback JSONL")
    hive_root.add_argument("--registry")
    hive_root.add_argument("--task-category")
    hive_root.add_argument("--effort", default="observed")
    hive_root.add_argument("--execution", default="hive_agent_loop")
    hive_root.add_argument(
        "--apply",
        action="store_true",
        help="Append eligible observations; without this flag the command is preview-only",
    )
    hive_root.add_argument("--output", help="Optional Markdown discovery/import report")
    hive_root.add_argument("--json-output")
    hive_root.set_defaults(func=command_feedback_import_hive_root)

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
