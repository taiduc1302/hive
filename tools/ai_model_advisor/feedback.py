from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

_OUTCOME_VALUE = {"success": 1.0, "partial": 0.5, "failure": 0.0}
EXACT_FEEDBACK_MIN = 3
CROSS_CONFIG_FEEDBACK_MIN = 6
PAIRED_EFFICIENCY_MIN = 3


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
    task_id: str | None = None
    source_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOME_VALUE:
            raise ValueError("outcome must be success, partial, or failure")
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.latency_seconds is not None and self.latency_seconds <= 0:
            raise ValueError("latency_seconds must be > 0 when provided")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd must be >= 0 when provided")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class FeedbackStore:
    """Small local JSONL store for empirical model outcomes.

    Quality adjustments are deliberately conservative: fewer than three exact
    configuration observations have no routing effect. Evidence from other
    effort/execution configurations of the same model is used only after six
    category-compatible observations exist. Larger samples are shrunk toward
    neutral so a short streak cannot dominate the static capability model.
    Category-tagged feedback never leaks into a different task category.

    Cost and latency are stricter. They affect routing only when the same
    ``task_id`` was successfully attempted by the candidate configuration and
    at least one alternative configuration. This paired comparison prevents a
    quick small task from being treated as evidence against a slower large one.

    ``source_id`` is metadata only. Importers use it as an idempotency key so
    re-reading the same trace does not duplicate evidence.
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
        for line_number, line in enumerate(
            target.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
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

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(record.source_id for record in self.records if record.source_id)

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
            if len(exact_category) >= EXACT_FEEDBACK_MIN:
                return exact_category
            if len(category_records) >= CROSS_CONFIG_FEEDBACK_MIN:
                return category_records

            # Backward compatibility for feedback captured before categories
            # existed. Never borrow evidence from a different named category.
            untagged = tuple(record for record in model_records if record.task_category is None)
            exact_untagged = tuple(
                record
                for record in untagged
                if record.effort == effort and record.execution_mode == execution_mode
            )
            if len(exact_untagged) >= EXACT_FEEDBACK_MIN:
                return exact_untagged
            if len(untagged) >= CROSS_CONFIG_FEEDBACK_MIN:
                return untagged
            return exact_category or exact_untagged

        exact = tuple(
            record
            for record in model_records
            if record.effort == effort and record.execution_mode == execution_mode
        )
        if len(exact) >= EXACT_FEEDBACK_MIN:
            return exact
        if len(model_records) >= CROSS_CONFIG_FEEDBACK_MIN:
            return model_records
        return exact

    def adjustment(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
    ) -> float:
        records = self.matching(model_id, effort, execution_mode, task_category)
        if len(records) < EXACT_FEEDBACK_MIN:
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

    @staticmethod
    def _config_key(record: UsageRecord) -> tuple[str, str, str]:
        return record.model_id, record.effort, record.execution_mode

    def _paired_efficiency_scores(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None,
        latency_sensitivity: float,
        cost_sensitivity: float,
    ) -> list[float]:
        target_key = (model_id, effort, execution_mode)
        candidate_by_task: dict[str, list[UsageRecord]] = defaultdict(list)
        for record in self.records:
            if self._config_key(record) != target_key:
                continue
            if record.outcome != "success" or not record.task_id:
                continue
            if task_category and record.task_category != task_category:
                continue
            candidate_by_task[record.task_id].append(record)

        latency_weight = max(0.0, min(1.0, latency_sensitivity / 5.0))
        cost_weight = max(0.0, min(1.0, cost_sensitivity / 5.0))
        task_scores: list[float] = []

        for task_id, candidate_records in candidate_by_task.items():
            peers = [
                record
                for record in self.records
                if record.task_id == task_id
                and record.outcome == "success"
                and self._config_key(record) != target_key
                and (not task_category or record.task_category == task_category)
            ]
            if not peers:
                continue

            metric_scores: list[tuple[float, float]] = []
            candidate_latency = [
                record.latency_seconds
                for record in candidate_records
                if record.latency_seconds is not None
            ]
            peer_latency = [record.latency_seconds for record in peers if record.latency_seconds is not None]
            if candidate_latency and peer_latency and latency_weight > 0:
                reference = median(peer_latency)
                if reference > 0:
                    delta = (reference - median(candidate_latency)) / reference
                    metric_scores.append((max(-0.5, min(0.5, delta)), latency_weight))

            candidate_cost = [record.cost_usd for record in candidate_records if record.cost_usd is not None]
            peer_cost = [record.cost_usd for record in peers if record.cost_usd is not None]
            if candidate_cost and peer_cost and cost_weight > 0:
                reference = median(peer_cost)
                if reference > 0:
                    delta = (reference - median(candidate_cost)) / reference
                    metric_scores.append((max(-0.5, min(0.5, delta)), cost_weight))

            if not metric_scores:
                continue
            weighted_total = sum(score * weight for score, weight in metric_scores)
            total_weight = sum(weight for _, weight in metric_scores)
            task_scores.append(weighted_total / total_weight)

        return task_scores

    def efficiency_adjustment(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
        latency_sensitivity: float = 3.0,
        cost_sensitivity: float = 3.0,
    ) -> float:
        scores = self._paired_efficiency_scores(
            model_id,
            effort,
            execution_mode,
            task_category,
            latency_sensitivity,
            cost_sensitivity,
        )
        if len(scores) < PAIRED_EFFICIENCY_MIN:
            return 0.0
        sample_weight = min(1.0, (len(scores) - 2) / 6.0)
        observed = sum(scores) / len(scores)
        return round(observed * 6.0 * sample_weight, 3)

    def summary(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
    ) -> tuple[int, float]:
        records = self.matching(model_id, effort, execution_mode, task_category)
        return len(records), self.adjustment(model_id, effort, execution_mode, task_category)

    def efficiency_summary(
        self,
        model_id: str,
        effort: str,
        execution_mode: str,
        task_category: str | None = None,
        latency_sensitivity: float = 3.0,
        cost_sensitivity: float = 3.0,
    ) -> tuple[int, float]:
        scores = self._paired_efficiency_scores(
            model_id,
            effort,
            execution_mode,
            task_category,
            latency_sensitivity,
            cost_sensitivity,
        )
        return len(scores), self.efficiency_adjustment(
            model_id,
            effort,
            execution_mode,
            task_category,
            latency_sensitivity,
            cost_sensitivity,
        )
