from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .feedback import FeedbackStore
from .models import ModelProfile, Recommendation, WorkloadProfile
from .registry import ModelRegistry

_EFFORT_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
}
_MODE_DEPTH = {
    "single": 0,
    "subagents": 1,
    "chatgpt_work": 2,
    "multi_agent": 2,
    "dynamic_workflow": 2,
    "ultracode": 3,
}


class RecommendationEngine:
    def __init__(self, registry: ModelRegistry, feedback: FeedbackStore | None = None) -> None:
        self.registry = registry
        self.feedback = feedback or FeedbackStore()

    @staticmethod
    def _difficulty(workload: WorkloadProfile) -> float:
        return (
            workload.reasoning * 0.30
            + workload.ambiguity * 0.22
            + workload.agentic * 0.20
            + workload.breadth * 0.18
            + workload.coding * 0.10
        )

    @classmethod
    def _effort(cls, model: ModelProfile, workload: WorkloadProfile) -> str:
        efforts = set(model.efforts)
        difficulty = cls._difficulty(workload)
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

    @classmethod
    def _configuration_adjustment(
        cls,
        workload: WorkloadProfile,
        effort: str,
        mode: str,
        preferred_effort: str,
        preferred_mode: str,
    ) -> float:
        """Static prior for effort/mode deviations before personal evidence.

        The preferred heuristic receives zero adjustment. Nearby alternatives
        remain viable but pay a modest penalty, which exact empirical evidence
        can overcome. The penalty grows when a configuration spends more
        reasoning/orchestration under high cost/latency sensitivity, or when it
        removes reasoning/orchestration from a difficult/agentic workload.
        """
        preferred_effort_rank = _EFFORT_ORDER.get(preferred_effort, 2)
        effort_rank = _EFFORT_ORDER.get(effort, preferred_effort_rank)
        effort_distance = abs(effort_rank - preferred_effort_rank)
        effort_penalty = 0.0
        if effort_distance:
            if effort_rank > preferred_effort_rank:
                pressure = 0.75 + 0.10 * (
                    workload.cost_sensitivity + workload.latency_sensitivity
                )
            else:
                pressure = 0.85 + 0.10 * cls._difficulty(workload)
            effort_penalty = effort_distance * 0.90 * pressure

        preferred_mode_depth = _MODE_DEPTH.get(preferred_mode, 0)
        mode_depth = _MODE_DEPTH.get(mode, preferred_mode_depth)
        mode_distance = abs(mode_depth - preferred_mode_depth)
        mode_penalty = 0.0
        if mode != preferred_mode:
            if mode_distance == 0:
                mode_penalty = 0.75
            elif mode_depth > preferred_mode_depth:
                pressure = 0.85 + 0.10 * (
                    workload.cost_sensitivity + workload.latency_sensitivity
                )
                mode_penalty = mode_distance * 1.20 * pressure
            else:
                pressure = 0.85 + 0.10 * (workload.agentic + workload.breadth)
                mode_penalty = mode_distance * 1.20 * pressure

        return round(-min(8.0, effort_penalty + mode_penalty), 3)

    def _explain(
        self,
        model: ModelProfile,
        workload: WorkloadProfile,
        effort: str,
        mode: str,
        preferred_effort: str,
        preferred_mode: str,
        task_category: str | None,
        quality_adjustment: float,
        efficiency_adjustment: float,
        configuration_adjustment: float,
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

        quality_samples, _ = self.feedback.summary(
            model.model_id,
            effort,
            mode,
            task_category,
        )
        efficiency_samples, _ = self.feedback.efficiency_summary(
            model.model_id,
            effort,
            mode,
            task_category,
            workload.latency_sensitivity,
            workload.cost_sensitivity,
        )
        category_label = f" for {task_category}" if task_category else ""
        if quality_adjustment >= 1.0:
            reasons.append(
                f"Personal outcome history{category_label} improves this configuration's score "
                f"({quality_samples} observations)"
            )
        elif quality_adjustment <= -1.0:
            tradeoffs.append(
                f"Personal outcome history{category_label} reduces confidence in this configuration "
                f"({quality_samples} observations)"
            )

        if efficiency_adjustment >= 0.5:
            reasons.append(
                f"Paired same-task cost/latency history favors this configuration "
                f"({efficiency_samples} comparable tasks)"
            )
        elif efficiency_adjustment <= -0.5:
            tradeoffs.append(
                "Paired same-task cost/latency history is unfavorable "
                f"({efficiency_samples} comparable tasks)"
            )

        if effort != preferred_effort or mode != preferred_mode:
            if quality_adjustment + efficiency_adjustment > abs(configuration_adjustment):
                reasons.append(
                    "Repeated empirical evidence is strong enough to override the static effort/mode prior"
                )
            tradeoffs.append(
                f"Static workload prior prefers {preferred_effort}/{preferred_mode}; "
                f"this configuration carries {configuration_adjustment:+.3f} prior adjustment"
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

    def _score_configuration(
        self,
        model: ModelProfile,
        workload: WorkloadProfile,
        effort: str,
        mode: str,
        preferred_effort: str,
        preferred_mode: str,
        task_category: str | None,
    ) -> Recommendation:
        quality_adjustment = self.feedback.adjustment(
            model.model_id,
            effort,
            mode,
            task_category,
        )
        efficiency_adjustment = self.feedback.efficiency_adjustment(
            model.model_id,
            effort,
            mode,
            task_category,
            workload.latency_sensitivity,
            workload.cost_sensitivity,
        )
        empirical_adjustment = max(
            -10.0,
            min(10.0, quality_adjustment + efficiency_adjustment),
        )
        model_score = self._base_score(model, workload)
        configuration_adjustment = self._configuration_adjustment(
            workload,
            effort,
            mode,
            preferred_effort,
            preferred_mode,
        )
        base_score = model_score + configuration_adjustment
        score = base_score + empirical_adjustment
        reasons, tradeoffs = self._explain(
            model,
            workload,
            effort,
            mode,
            preferred_effort,
            preferred_mode,
            task_category,
            quality_adjustment,
            efficiency_adjustment,
            configuration_adjustment,
        )
        return Recommendation(
            provider=model.provider,
            model_id=model.model_id,
            label=model.label,
            effort=effort,
            execution_mode=mode,
            score=round(score, 2),
            confidence=0.0,
            reasons=reasons,
            tradeoffs=tradeoffs,
            model_score=round(model_score, 2),
            configuration_adjustment=configuration_adjustment,
            base_score=round(base_score, 2),
            quality_adjustment=quality_adjustment,
            efficiency_adjustment=efficiency_adjustment,
            preferred_effort=preferred_effort,
            preferred_execution_mode=preferred_mode,
        )

    def _best_configuration(
        self,
        model: ModelProfile,
        workload: WorkloadProfile,
        task_category: str | None,
    ) -> Recommendation:
        preferred_effort = self._effort(model, workload)
        preferred_mode = self._execution_mode(model, workload)
        configurations = [
            self._score_configuration(
                model,
                workload,
                effort,
                mode,
                preferred_effort,
                preferred_mode,
                task_category,
            )
            for effort in model.efforts
            for mode in model.execution_modes
        ]
        return max(
            configurations,
            key=lambda item: (
                item.score,
                item.configuration_adjustment,
                item.effort == preferred_effort,
                item.execution_mode == preferred_mode,
            ),
        )

    def recommend(
        self,
        workload: WorkloadProfile,
        providers: Iterable[str] | None = None,
        include_limited: bool = False,
        top_n: int = 3,
    ) -> list[Recommendation]:
        task_category = self._primary_category(workload)
        scored = [
            self._best_configuration(model, workload, task_category)
            for model in self.registry.candidates(
                providers=providers,
                include_limited=include_limited,
            )
        ]
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
