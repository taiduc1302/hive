from __future__ import annotations

from tools.ai_model_advisor.execution_targets import (
    configuration_blockers,
    profile_for_host,
    target_binding_blockers,
    target_catalog,
    target_catalog_markdown,
)


def test_execution_target_catalog_is_machine_discoverable() -> None:
    catalog = target_catalog()

    assert set(catalog) == {"hive", "provider_api"}
    assert catalog["hive"]["runner_module"] == "tools.ai_model_advisor.hive_litellm_adapter"
    assert catalog["provider_api"]["runner_module"] == (
        "tools.ai_model_advisor.provider_api_adapter"
    )
    assert catalog["hive"]["requires_external_judge"] is True
    assert catalog["provider_api"]["execution_modes"] == ["single"]
    assert "AI Model Advisor Execution Targets" in target_catalog_markdown()


def test_shared_configuration_rules_are_consistent_across_builtin_targets() -> None:
    for host in ("hive", "provider_api"):
        profile = profile_for_host(host)
        assert configuration_blockers(
            {
                "provider": "openai",
                "model_id": "gpt-5.6-terra",
                "effort": "medium",
                "execution_mode": "single",
            },
            profile,
        ) == []
        blockers = configuration_blockers(
            {
                "provider": "other",
                "model_id": "model",
                "effort": "default",
                "execution_mode": "subagents",
            },
            profile,
        )
        assert len(blockers) == 2


def test_target_binding_rules_come_from_profile_contract() -> None:
    profile = profile_for_host("provider_api")
    assert target_binding_blockers(profile.binding(), profile, allow_unbound=False) == []
    assert target_binding_blockers(None, profile, allow_unbound=False) == [
        "plan is not bound to an execution target"
    ]
    blockers = target_binding_blockers(
        {
            "host": "hive",
            "adapter": "hive_litellm",
            "adapter_contract_version": 1,
        },
        profile,
        allow_unbound=False,
    )
    assert any("host='hive'" in blocker for blocker in blockers)
    assert any("adapter='hive_litellm'" in blocker for blocker in blockers)
