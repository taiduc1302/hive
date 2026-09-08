from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model_id: str
    label: str
    status: str
    context_tokens: int
    max_output_tokens: int
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    efforts: tuple[str, ...]
    default_effort: str
    execution_modes: tuple[str, ...]
    capabilities: dict[str, float]
    source_ids: tuple[str, ...] = ()
    notes: str = ""

    @property
    def cost_efficiency(self) -> float:
        return float(self.capabilities.get("cost_efficiency", 3.0))


@dataclass
class WorkloadProfile:
    coding: float = 1.0
    reasoning: float = 1.0
    agentic: float = 1.0
    ambiguity: float = 1.0
    breadth: float = 1.0
    parallelism: float = 1.0
    latency_sensitivity: float = 3.0
    cost_sensitivity: float = 3.0
    volume: float = 1.0
    categories: dict[str, int] = field(default_factory=dict)
    activity_count: int = 0
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    provider: str
    model_id: str
    label: str
    effort: str
    execution_mode: str
    score: float
    confidence: float
    reasons: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    model_score: float = 0.0
    configuration_adjustment: float = 0.0
    base_score: float = 0.0
    quality_adjustment: float = 0.0
    efficiency_adjustment: float = 0.0
    preferred_effort: str = ""
    preferred_execution_mode: str = ""

    @property
    def raw_empirical_adjustment(self) -> float:
        return round(self.quality_adjustment + self.efficiency_adjustment, 3)

    @property
    def empirical_adjustment(self) -> float:
        return round(self.score - self.base_score, 3)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_empirical_adjustment"] = self.raw_empirical_adjustment
        payload["empirical_adjustment"] = self.empirical_adjustment
        return payload
