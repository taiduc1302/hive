from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionTargetProfile:
    host: str
    adapter: str
    adapter_contract_version: int
    preflight_module: str
    runner_module: str
    supported_providers: tuple[str, ...]
    execution_modes: tuple[str, ...]
    requires_external_judge: bool = True
    credential_env_by_provider: dict[str, str] = field(default_factory=dict)
    evidence_method: str = "applied_configuration"

    def binding(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "adapter": self.adapter,
            "adapter_contract_version": self.adapter_contract_version,
            "preflight_module": self.preflight_module,
        }

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["supported_providers"] = list(self.supported_providers)
        payload["execution_modes"] = list(self.execution_modes)
        return payload


_PROFILES: tuple[ExecutionTargetProfile, ...] = (
    ExecutionTargetProfile(
        host="hive",
        adapter="hive_litellm",
        adapter_contract_version=1,
        preflight_module="tools.ai_model_advisor.hive_experiment_preflight",
        runner_module="tools.ai_model_advisor.hive_litellm_adapter",
        supported_providers=("openai", "anthropic"),
        execution_modes=("single",),
        requires_external_judge=True,
        credential_env_by_provider={
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        },
        evidence_method="post_transform_wire_and_applied_configuration",
    ),
    ExecutionTargetProfile(
        host="provider_api",
        adapter="provider_api",
        adapter_contract_version=1,
        preflight_module="tools.ai_model_advisor.provider_experiment_preflight",
        runner_module="tools.ai_model_advisor.provider_api_adapter",
        supported_providers=("openai", "anthropic"),
        execution_modes=("single",),
        requires_external_judge=True,
        credential_env_by_provider={
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        },
        evidence_method="applied_configuration",
    ),
)

_BY_HOST = {profile.host: profile for profile in _PROFILES}
_BY_ADAPTER = {profile.adapter: profile for profile in _PROFILES}


class ExecutionTargetCatalogError(ValueError):
    pass


def target_profiles() -> tuple[ExecutionTargetProfile, ...]:
    return _PROFILES


def target_catalog() -> dict[str, dict[str, Any]]:
    return {profile.host: profile.as_dict() for profile in _PROFILES}


def profile_for_host(host: str) -> ExecutionTargetProfile:
    profile = _BY_HOST.get(host)
    if profile is None:
        raise ExecutionTargetCatalogError(
            f"Unsupported execution target {host!r}; supported targets: {', '.join(sorted(_BY_HOST))}"
        )
    return profile


def profile_for_adapter(adapter: str) -> ExecutionTargetProfile:
    profile = _BY_ADAPTER.get(adapter)
    if profile is None:
        raise ExecutionTargetCatalogError(f"Unknown execution adapter {adapter!r}")
    return profile


def target_binding_blockers(
    target: dict[str, Any] | None,
    profile: ExecutionTargetProfile,
    *,
    allow_unbound: bool,
) -> list[str]:
    if not isinstance(target, dict):
        return [] if allow_unbound else ["plan is not bound to an execution target"]

    blockers: list[str] = []
    if target.get("host") != profile.host:
        blockers.append(
            f"plan is bound to host={target.get('host')!r}, not {profile.host!r}"
        )
    if target.get("adapter") != profile.adapter:
        blockers.append(
            f"plan is bound to adapter={target.get('adapter')!r}, not {profile.adapter!r}"
        )
    if target.get("adapter_contract_version") != profile.adapter_contract_version:
        blockers.append(
            "plan uses unsupported adapter contract version "
            f"{target.get('adapter_contract_version')!r}; expected {profile.adapter_contract_version}"
        )
    return blockers


def configuration_blockers(
    configuration: dict[str, Any],
    profile: ExecutionTargetProfile,
) -> list[str]:
    provider = str(configuration.get("provider") or "")
    execution_mode = str(configuration.get("execution_mode") or "")
    model_id = str(configuration.get("model_id") or "")
    effort = str(configuration.get("effort") or "")

    blockers: list[str] = []
    if provider not in profile.supported_providers:
        blockers.append(
            f"adapter supports {', '.join(profile.supported_providers)}, not {provider or 'missing'}"
        )
    if execution_mode not in profile.execution_modes:
        blockers.append(
            f"adapter supports execution_mode={','.join(profile.execution_modes)}, "
            f"not {execution_mode or 'missing'}"
        )
    if not model_id:
        blockers.append("model_id is missing")
    if not effort:
        blockers.append("effort is missing")
    return blockers
