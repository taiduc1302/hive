from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

_OUTCOME_VALUE = {"success": 1.0, "partial": 0.5, "failure": 0.0}


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    model_id: str
    effort: str
    execution_mode: str
    outcome: str
    retries: int = 0
    latency_seconds: float | None = None
    cost_usd: float | None = None
    task_category: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOME_VALUE:
            raise ValueError("outcome must be success, partial, or failure")
        if self.retries < 0:
            raise ValueError("retries must be >= 0")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class FeedbackStore:
    """Small local JSONL store for empirical model outcomes.

    Adjustments are deliberately conservative: fewer than three matching
    observations have no routing effect, and larger samples are shrunk toward
    neutral so a short streak cannot dominate the static capability model.
    Category-tagged feedback never leaks into a different task category.
    """

    def __init__(self, records: Iterable[UsageRecord] = ()) -> None:
        self.records = tuple(records)

    @classmethod
    def load(cls, path: str | Path | None) -> FeedbackStore:
        if not path:
            return cls()
        target = Path(path)
        if not target.exists():
            return cls()
        records: list[UsageRecord] = []
        for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(UsageRecord(**json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid feedback record at line {line_number}: {exc}") from exc
        return cls(records)

    @staticmethod
    def append(path: str | Path, record: UsageRecord) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")

    def matching(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
    ) -> tuple[UsageRecord, ...]:
        model_records = tuple(record for record in self.records if record.model_id == model_id)

        if task_category:
            category_records = tuple(
                record for record in model_records if record.task_category == task_category
            )
            exact_category = tuple(
                record
                for record in category_records
                if record.effort == effort and record.execution_mode == execution_mode
            )
            if len(exact_category) >= 3:
                return exact_category
            if len(category_records) >= 3:
                return category_records

            # Backward compatibility for feedback captured before categories
            # existed. Never borrow evidence from a different named category.
            untagged = tuple(record for record in model_records if record.task_category is None)
            exact_untagged = tuple(
                record
                for record in untagged
                if record.effort == effort and record.execution_mode == execution_mode
            )
            if len(exact_untagged) >= 3:
                return exact_untagged
            return untagged

        exact = tuple(
            record
            for record in model_records
            if record.effort == effort and record.execution_mode == execution_mode
        )
        if len(exact) >= 3:
            return exact
        return model_records

    def adjustment(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
    ) -> float:
        records = self.matching(model_id, effort, execution_mode, task_category)
        if len(records) < 3:
            return 0.0
        observed = sum(_OUTCOME_VALUE[record.outcome] for record in records) / len(records)
        retry_penalty = min(
            0.20,
            sum(record.retries for record in records) / max(1, len(records)) * 0.05,
        )
        quality = max(0.0, observed - retry_penalty)
        sample_weight = min(1.0, (len(records) - 2) / 8.0)
        shrunk = 0.5 + (quality - 0.5) * sample_weight
        return round((shrunk - 0.5) * 16.0, 3)

    def summary(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
    ) -> tuple[int, float]:
        records = self.matching(model_id, effort, execution_mode, task_category)
        return len(records), self.adjustment(model_id, effort, execution_mode, task_category)
