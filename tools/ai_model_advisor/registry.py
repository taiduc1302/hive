from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import ModelProfile


DEFAULT_REGISTRY = Path(__file__).with_name("registry.json")


class RegistryError(ValueError):
    pass


class ModelRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_REGISTRY
        self.raw = self._load(self.path)
        self.as_of = str(self.raw.get("as_of", "unknown"))
        self.sources = {item["id"]: item for item in self.raw.get("official_sources", [])}
        self.models = tuple(self._parse_model(item) for item in self.raw.get("models", []))
        self._validate()

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise RegistryError("Registry root must be an object")
        return data

    @staticmethod
    def _parse_model(item: dict[str, Any]) -> ModelProfile:
        return ModelProfile(
            provider=str(item["provider"]),
            model_id=str(item["model_id"]),
            label=str(item["label"]),
            status=str(item.get("status", "active")),
            context_tokens=int(item.get("context_tokens", 0)),
            max_output_tokens=int(item.get("max_output_tokens", 0)),
            input_usd_per_mtok=float(item.get("input_usd_per_mtok", 0.0)),
            output_usd_per_mtok=float(item.get("output_usd_per_mtok", 0.0)),
            efforts=tuple(item.get("efforts", ())),
            default_effort=str(item.get("default_effort", "default")),
            execution_modes=tuple(item.get("execution_modes", ("single",))),
            capabilities={k: float(v) for k, v in item.get("capabilities", {}).items()},
            source_ids=tuple(item.get("source_ids", ())),
            notes=str(item.get("notes", "")),
        )

    def _validate(self) -> None:
        ids: set[tuple[str, str]] = set()
        for model in self.models:
            key = (model.provider, model.model_id)
            if key in ids:
                raise RegistryError(f"Duplicate model: {key}")
            ids.add(key)
            missing_sources = [source for source in model.source_ids if source not in self.sources]
            if missing_sources:
                raise RegistryError(f"Unknown source ids for {model.model_id}: {missing_sources}")
            for name, value in model.capabilities.items():
                if value < 1 or value > 5:
                    raise RegistryError(f"Capability {name} for {model.model_id} must be 1..5")

    def candidates(
        self,
        providers: Iterable[str] | None = None,
        include_limited: bool = False,
    ) -> tuple[ModelProfile, ...]:
        allowed = {item.lower() for item in providers} if providers else None
        result = []
        for model in self.models:
            if allowed and model.provider.lower() not in allowed:
                continue
            if model.status == "limited" and not include_limited:
                continue
            if model.status in {"retired", "deprecated"}:
                continue
            result.append(model)
        return tuple(result)
