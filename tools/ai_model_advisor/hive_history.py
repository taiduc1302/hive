from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .feedback import UsageRecord
from .hive_trace import HiveTraceImportReport, import_hive_trace
from .registry import ModelRegistry


@dataclass(frozen=True)
class HiveSessionTrace:
    session_id: str
    events_path: Path
    details_path: Path | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "events_path": str(self.events_path),
            "details_path": str(self.details_path) if self.details_path else None,
        }


@dataclass(frozen=True)
class HiveHistoryImportReport:
    sessions: tuple[HiveSessionTrace, ...]
    records: tuple[UsageRecord, ...]
    skipped_mixed_models: int = 0
    skipped_unknown_models: int = 0
    skipped_unknown_outcomes: int = 0
    ambiguous_runtime_details: int = 0
    corrupt_lines: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions_scanned": len(self.sessions),
            "sessions": [session.as_dict() for session in self.sessions],
            "records": [record.as_dict() for record in self.records],
            "skipped_mixed_models": self.skipped_mixed_models,
            "skipped_unknown_models": self.skipped_unknown_models,
            "skipped_unknown_outcomes": self.skipped_unknown_outcomes,
            "ambiguous_runtime_details": self.ambiguous_runtime_details,
            "corrupt_lines": self.corrupt_lines,
        }


def discover_hive_session_traces(root: str | Path) -> tuple[HiveSessionTrace, ...]:
    """Discover session-level Hive event logs below ``root``.

    A valid session trace must have the canonical shape
    ``.../sessions/<session_id>/events.jsonl``. Worker-local logs nested below
    a session do not match this shape and are deliberately excluded.
    ``logs/details.jsonl`` is attached when present but is not required.
    """
    base = Path(root).expanduser()
    if not base.exists():
        raise FileNotFoundError(base)
    if not base.is_dir():
        raise NotADirectoryError(base)

    traces: list[HiveSessionTrace] = []
    for events_path in base.rglob("events.jsonl"):
        session_dir = events_path.parent
        if session_dir.parent.name != "sessions":
            continue
        details = session_dir / "logs" / "details.jsonl"
        traces.append(
            HiveSessionTrace(
                session_id=session_dir.name,
                events_path=events_path,
                details_path=details if details.is_file() else None,
            )
        )
    traces.sort(key=lambda item: str(item.events_path))
    return tuple(traces)


def import_hive_history(
    root: str | Path,
    registry: ModelRegistry,
    task_category: str | None = None,
    effort: str = "observed",
    execution_mode: str = "hive_agent_loop",
) -> HiveHistoryImportReport:
    sessions = discover_hive_session_traces(root)
    records: list[UsageRecord] = []
    counters = {
        "skipped_mixed_models": 0,
        "skipped_unknown_models": 0,
        "skipped_unknown_outcomes": 0,
        "ambiguous_runtime_details": 0,
        "corrupt_lines": 0,
    }

    for session in sessions:
        report: HiveTraceImportReport = import_hive_trace(
            session.events_path,
            registry,
            details_path=session.details_path,
            task_category=task_category,
            effort=effort,
            execution_mode=execution_mode,
        )
        records.extend(report.records)
        for key in counters:
            counters[key] += int(getattr(report, key))

    return HiveHistoryImportReport(
        sessions=sessions,
        records=tuple(records),
        **counters,
    )
