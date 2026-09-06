from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .feedback import FeedbackStore
from .models import ModelProfile, Recommendation, WorkloadProfile
from .registry import ModelRegistry


class RecommendationEngine:
    def __init__(self, registry: ModelRegistry, feedback: FeedbackStore | None = None) -> None:
        self.registry = registry
        self.feedback = feedback or FeedbackStore()

    @staticmethod
    def _effort(model: ModelProfile, workload: WorkloadProfile) -> str:
        efforts = set(model.efforts)
        difficulty = (
            workload.reasoning * 0.30
            + workload.ambiguity * 0.22
            + workload.agentic * 0.20
            + workload.breadth * 0.18
            + workload.coding * 0.10
        )
        long_horizon = workload.agentic >= 4.0 or workload.breadth >= 4.3
        if difficulty >= 4.6:
            preferences = ["xhigh", "max"] if long_horizon else ["max", "xhigh"]
        elif difficulty >= 3.8:
            preferences = ["xhigh", "high"] if long_horizon else ["high", "xhigh"]
        elif difficulty >= 2.8:
            preferences = ["high", "medium"]
        elif workload.latency_sensitivity >= 4.0 or workload.cost_sensitivity >= 4.0:
            preferences = ["low", "none", "medium"]
        else:
            preferences = ["medium", "high", "low"]
        for item in preferences:
            if item in efforts:
                return item
        if model.default_effort in efforts:
            return model.default_effort
        return next(iter(model.efforts), "default")

    @staticmethod
    def _execution_mode(model: ModelProfile, workload: WorkloadProfile) -> str:
        modes = set(model.execution_modes)
        if workload.parallelism >= 4.2 and workload.breadth >= 4.0:
            for mode in ("ultracode", "dynamic_workflow", "multi_agent", "chatgpt_work"):
                if mode in modes:
                    return mode
        if workload.parallelism >= 3.1 and "subagents" in modes:
            return "subagents"
        return "single" if "single" in modes else next(iter(model.execution_modes), "single")

    @staticmethod
    def _primary_category(workload: WorkloadProfile) -> str | None:
        if not workload.categories:
            return None
        return max(workload.categories.items(), key=lambda item: (item[1], item[0]))[0]

    @staticmethod
    def _base_score(model: ModelProfile, workload: WorkloadProfile) -> float:
        c = model.capabilities
        need_total = workload.reasoning + workload.coding + workload.agentic + workload.breadth
        quality = (
            c.get("reasoning", 3) * workload.reasoning * 1.15
            + c.get("coding", 3) * workload.coding * 1.10
            + c.get("agentic", 3) * workload.agentic * 1.05
            + c.get("long_horizon", 3) * workload.breadth * 0.80
            + c.get("ambiguity", 3) * workload.ambiguity * 0.75
        )
        efficiency = (
            c.get("speed", 3) * workload.latency_sensitivity * 0.55
            + c.get("cost_efficiency", 3) * workload.cost_sensitivity * 0.70
        )
        overkill = max(
            0.0,
            (sum(c.get(key, 3) for key in ("reasoning", "coding", "agentic")) / 3)
            - (need_total / 4),
        )
        overkill_penalty = overkill * (workload.cost_sensitivity + workload.latency_sensitivity) * 0.75
        limited_penalty = 18.0 if model.status == "limited" else 0.0
        return quality + efficiency - overkill_penalty - limited_penalty

    def _explain(
        self,
        model: ModelProfile,
        workload: WorkloadProfile,
        effort: str,
        mode: str,
        task_category: str | None,
        feedback_adjustment: float,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        reasons: list[str] = []
        tradeoffs: list[str] = []
        if workload.coding >= 3.5 and model.capabilities.get("coding", 0) >= 4:
            reasons.append("Strong fit for the coding-heavy part of recent activity")
        if workload.reasoning >= 3.8 and model.capabilities.get("reasoning", 0) >= 4.5:
            reasons.append("High reasoning demand matches this model's capability tier")
        if workload.agentic >= 3.5 and model.capabilities.get("agentic", 0) >= 4:
            reasons.append("Recent work is agentic/long-running rather than single-turn")
        if mode in {"ultracode", "dynamic_workflow", "multi_agent", "chatgpt_work"}:
            reasons.append("Repository breadth and parallelizable work justify structured orchestration")
        if effort in {"xhigh", "max"}:
            reasons.append(f"{effort} is reserved here for unusually difficult or long-horizon work")
        sample_count, _ = self.feedback.summary(
            model.model_id,
            effort,
            mode,
            task_category,
        )
        category_label = f" for {task_category}" if task_category else ""
        if feedback_adjustment >= 1.0:
            reasons.append(
                f"Personal history{category_label} improves this configuration's score "
                f"({sample_count} observations)"
            )
        elif feedback_adjustment <= -1.0:
            tradeoffs.append(
                f"Personal history{category_label} reduces confidence in this configuration "
                f"({sample_count} observations)"
            )
        if model.capabilities.get("speed", 3) <= 2:
            tradeoffs.append("Expect higher latency")
        if model.cost_efficiency <= 2:
            tradeoffs.append("Expensive for routine work; use only when the quality gain matters")
        if effort == "max":
            tradeoffs.append("Max effort can overthink and consume substantially more tokens")
        if mode in {"ultracode", "dynamic_workflow", "multi_agent", "chatgpt_work"}:
            tradeoffs.append("Multi-agent/workflow execution can multiply token usage")
        if not reasons:
            reasons.append("Balanced match across capability, speed, and cost")
        return tuple(reasons[:4]), tuple(tradeoffs[:3])

    def recommend(
        self,
        workload: WorkloadProfile,
        providers: Iterable[str] | None = None,
        include_limited: bool = False,
        top_n: int = 3,
    ) -> list[Recommendation]:
        scored: list[Recommendation] = []
        task_category = self._primary_category(workload)
        for model in self.registry.candidates(providers=providers, include_limited=include_limited):
            effort = self._effort(model, workload)
            mode = self._execution_mode(model, workload)
            feedback_adjustment = self.feedback.adjustment(
                model.model_id,
                effort,
                mode,
                task_category,
            )
            score = self._base_score(model, workload) + feedback_adjustment
            reasons, tradeoffs = self._explain(
                model,
                workload,
                effort,
                mode,
                task_category,
                feedback_adjustment,
            )
            scored.append(
                Recommendation(
                    provider=model.provider,
                    model_id=model.model_id,
                    label=model.label,
                    effort=effort,
                    execution_mode=mode,
                    score=round(score, 2),
                    confidence=0.0,
                    reasons=reasons,
                    tradeoffs=tradeoffs,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        if not scored:
            return []
        best = scored[0].score
        second = scored[1].score if len(scored) > 1 else best - 5
        spread = max(1.0, abs(best - second))
        activity_factor = min(1.0, 0.35 + workload.activity_count / 25.0)
        confidence = min(0.96, 0.58 + min(0.25, spread / 40.0) + 0.13 * activity_factor)
        return [
            replace(item, confidence=round(max(0.45, confidence - index * 0.08), 2))
            for index, item in enumerate(scored[:top_n])
        ]
