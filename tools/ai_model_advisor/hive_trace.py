from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .feedback import FeedbackStore, UsageRecord
from .models import ModelProfile
from .registry import ModelRegistry


@dataclass(frozen=True)
class HiveTraceImportReport:
    records: tuple[UsageRecord, ...]
    skipped_mixed_models: int = 0
    skipped_unknown_models: int = 0
    skipped_unknown_outcomes: int = 0
    ambiguous_runtime_details: int = 0
    corrupt_lines: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [record.as_dict() for record in self.records],
            "skipped_mixed_models": self.skipped_mixed_models,
            "skipped_unknown_models": self.skipped_unknown_models,
            "skipped_unknown_outcomes": self.skipped_unknown_outcomes,
            "ambiguous_runtime_details": self.ambiguous_runtime_details,
            "corrupt_lines": self.corrupt_lines,
        }


@dataclass(frozen=True)
class FeedbackAppendResult:
    appended: int
    duplicates: int


@dataclass
class _TraceGroup:
    execution_id: str
    node_id: str
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class _ReadResult:
    rows: tuple[dict[str, Any], ...]
    corrupt_lines: int


def _read_jsonl(path: str | Path | None) -> _ReadResult:
    if not path:
        return _ReadResult((), 0)
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    rows: list[dict[str, Any]] = []
    corrupt = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            corrupt += 1
    return _ReadResult(tuple(rows), corrupt)


def _normalize_model(raw_model: str, registry: ModelRegistry) -> ModelProfile | None:
    raw = raw_model.strip().lower()
    if not raw:
        return None
    matches = [
        model
        for model in registry.models
        if raw == model.model_id.lower()
        or raw.endswith("/" + model.model_id.lower())
        or raw.endswith(":" + model.model_id.lower())
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _runtime_details_by_node(rows: tuple[dict[str, Any], ...]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        node_id = str(row.get("node_id") or "")
        if node_id:
            result[node_id].append(row)
    return result


def _outcome_from_detail(detail: dict[str, Any] | None) -> str | None:
    if not detail:
        return None
    success = bool(detail.get("success"))
    exit_status = str(detail.get("exit_status") or "").lower()
    if success and exit_status not in {"failure", "stalled", "guard_failure", "ghost_stream"}:
        return "success"
    if exit_status in {"escalated", "paused"}:
        return "partial"
    if not success or exit_status in {"failure", "stalled", "guard_failure", "ghost_stream"}:
        return "failure"
    return None


def _outcome_from_events(group: _TraceGroup, execution_group_count: int) -> str | None:
    verdicts = [
        str((event.get("data") or {}).get("action") or "").upper()
        for event in group.events
        if event.get("type") == "judge_verdict"
    ]
    verdicts = [verdict for verdict in verdicts if verdict]
    if verdicts:
        if verdicts[-1] == "ACCEPT":
            return "success"
        if verdicts[-1] == "ESCALATE":
            return "partial"

    if any(event.get("type") in {"node_stalled", "worker_failed"} for event in group.events):
        return "failure"

    # Execution-level terminal events do not name a node. They are safe to
    # attribute only when this execution contains exactly one LLM node.
    if execution_group_count == 1:
        types = {str(event.get("type") or "") for event in group.events}
        if "execution_completed" in types:
            return "success"
        if "execution_failed" in types:
            return "failure"
    return None


def _groups_from_events(rows: tuple[dict[str, Any], ...], node_filter: str | None) -> list[_TraceGroup]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    execution_terminals: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in rows:
        event_type = str(event.get("type") or "")
        execution_id = str(event.get("execution_id") or "")
        node_id = str(event.get("node_id") or "")
        if not execution_id:
            continue
        if event_type in {"execution_completed", "execution_failed"}:
            execution_terminals[execution_id].append(event)
        if not node_id:
            continue
        if node_filter and node_id != node_filter:
            continue
        grouped[(execution_id, node_id)].append(event)

    groups: list[_TraceGroup] = []
    for (execution_id, node_id), events in grouped.items():
        if not any(event.get("type") == "llm_turn_complete" for event in events):
            continue
        groups.append(
            _TraceGroup(
                execution_id=execution_id,
                node_id=node_id,
                events=[*events, *execution_terminals.get(execution_id, ())],
            )
        )
    return groups


def import_hive_trace(
    events_path: str | Path,
    registry: ModelRegistry,
    details_path: str | Path | None = None,
    task_category: str | None = None,
    effort: str = "observed",
    execution_mode: str = "hive_agent_loop",
    node_id: str | None = None,
    task_id: str | None = None,
) -> HiveTraceImportReport:
    """Convert persisted Hive telemetry into conservative UsageRecords.

    ``events_path`` is the session ``events.jsonl`` written by EventBus. It is
    the authoritative source for model and USD cost. ``details_path`` is the
    optional runtime ``logs/details.jsonl`` and supplies node outcome, retries,
    and wall-clock latency when a unique node-detail row can be joined safely.

    A mixed-model node is skipped rather than assigning the combined outcome
    or cost to one model. A user-supplied ``task_id`` is accepted only together
    with ``node_id`` so a benchmark ID cannot accidentally be stamped on every
    node in a session.
    """
    if task_id and not node_id:
        raise ValueError("--task-id requires --node-id when importing a Hive trace")

    events_read = _read_jsonl(events_path)
    details_read = _read_jsonl(details_path)
    details_by_node = _runtime_details_by_node(details_read.rows)
    groups = _groups_from_events(events_read.rows, node_id)
    groups_per_execution: dict[str, int] = defaultdict(int)
    for group in groups:
        groups_per_execution[group.execution_id] += 1

    records: list[UsageRecord] = []
    mixed_models = 0
    unknown_models = 0
    unknown_outcomes = 0
    ambiguous_details = 0

    for group in groups:
        turns = [event for event in group.events if event.get("type") == "llm_turn_complete"]
        normalized: list[ModelProfile] = []
        has_unknown_model = False
        for turn in turns:
            raw_model = str((turn.get("data") or {}).get("model") or "")
            model = _normalize_model(raw_model, registry)
            if model is None:
                has_unknown_model = True
                break
            normalized.append(model)
        if has_unknown_model or not normalized:
            unknown_models += 1
            continue

        identities = {(model.provider, model.model_id) for model in normalized}
        if len(identities) != 1:
            mixed_models += 1
            continue
        provider, model_id = next(iter(identities))

        detail_rows = details_by_node.get(group.node_id, [])
        detail: dict[str, Any] | None = None
        if len(detail_rows) == 1:
            detail = detail_rows[0]
        elif len(detail_rows) > 1:
            ambiguous_details += 1

        outcome = _outcome_from_detail(detail)
        if outcome is None:
            outcome = _outcome_from_events(group, groups_per_execution[group.execution_id])
        if outcome is None:
            unknown_outcomes += 1
            continue

        cost_values = []
        for turn in turns:
            raw_cost = (turn.get("data") or {}).get("cost_usd")
            if isinstance(raw_cost, (int, float)) and raw_cost > 0:
                cost_values.append(float(raw_cost))
        cost_usd = round(sum(cost_values), 8) if cost_values else None

        latency_seconds: float | None = None
        detail_latency = detail.get("latency_ms") if detail else None
        if isinstance(detail_latency, (int, float)) and detail_latency > 0:
            latency_seconds = round(float(detail_latency) / 1000.0, 3)

        judge_retries = sum(
            1
            for event in group.events
            if event.get("type") == "judge_verdict"
            and str((event.get("data") or {}).get("action") or "").upper() == "RETRY"
        )
        stream_retries = sum(1 for event in group.events if event.get("type") == "node_retry")
        detail_retries = int(detail.get("retry_count") or 0) if detail else 0
        retries = max(detail_retries, judge_retries) + stream_retries

        records.append(
            UsageRecord(
                provider=provider,
                model_id=model_id,
                effort=effort,
                execution_mode=execution_mode,
                outcome=outcome,
                retries=retries,
                latency_seconds=latency_seconds,
                cost_usd=cost_usd,
                task_category=task_category,
                task_id=task_id,
                source_id=f"hive:{group.execution_id}:{group.node_id}",
                note=(
                    f"Imported from Hive session telemetry; node={group.node_id}; "
                    f"llm_turns={len(turns)}"
                ),
            )
        )

    return HiveTraceImportReport(
        records=tuple(records),
        skipped_mixed_models=mixed_models,
        skipped_unknown_models=unknown_models,
        skipped_unknown_outcomes=unknown_outcomes,
        ambiguous_runtime_details=ambiguous_details,
        corrupt_lines=events_read.corrupt_lines + details_read.corrupt_lines,
    )


def append_imported_feedback(
    feedback_path: str | Path,
    records: tuple[UsageRecord, ...] | list[UsageRecord],
) -> FeedbackAppendResult:
    existing = FeedbackStore.load(feedback_path)
    source_ids = set(existing.source_ids)
    appended = 0
    duplicates = 0
    for record in records:
        if record.source_id and record.source_id in source_ids:
            duplicates += 1
            continue
        FeedbackStore.append(feedback_path, record)
        appended += 1
        if record.source_id:
            source_ids.add(record.source_id)
    return FeedbackAppendResult(appended=appended, duplicates=duplicates)


def import_report_markdown(
    report: HiveTraceImportReport,
    append_result: FeedbackAppendResult | None = None,
) -> str:
    lines = [
        "# Hive trace feedback import",
        "",
        f"Eligible records: **{len(report.records)}**",
    ]
    if append_result is not None:
        lines.extend(
            [
                f"Appended: **{append_result.appended}**",
                f"Already imported: **{append_result.duplicates}**",
            ]
        )
    lines.extend(
        [
            f"Skipped mixed-model nodes: **{report.skipped_mixed_models}**",
            f"Skipped unknown models: **{report.skipped_unknown_models}**",
            f"Skipped unknown outcomes: **{report.skipped_unknown_outcomes}**",
            f"Ambiguous runtime-detail joins: **{report.ambiguous_runtime_details}**",
            f"Corrupt JSONL lines ignored: **{report.corrupt_lines}**",
            "",
            "| Source | Model | Outcome | Retries | Latency | Cost |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for record in report.records:
        latency = f"{record.latency_seconds:.3f}s" if record.latency_seconds else "—"
        cost = f"${record.cost_usd:.6f}" if record.cost_usd is not None else "—"
        lines.append(
            f"| `{record.source_id}` | `{record.model_id}` | {record.outcome} | "
            f"{record.retries} | {latency} | {cost} |"
        )
    lines.extend(
        [
            "",
            "Mixed-model nodes are deliberately excluded. Unreported cost remains unknown rather than being treated as free.",
            "",
        ]
    )
    return "\n".join(lines)
