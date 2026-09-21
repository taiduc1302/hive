from tools.ai_model_advisor.hive_promotion_gate import build_hive_promotion_gate


def _preview(*, effort: str = "high"):
    return {
        "schema_version": 1,
        "host": "hive",
        "category": "debugging",
        "scope": "queen",
        "change_id": "debugging:test",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "requires_human_approval": True,
        "state": "ready_for_manual_hive_edit",
        "selected_transition": {
            "before": {
                "provider": "openai",
                "model_id": "old-model",
                "effort": "medium",
                "execution_mode": "single",
            },
            "after": {
                "provider": "openai",
                "model_id": "new-model",
                "effort": effort,
                "execution_mode": "single",
            },
            "rollback_to": {
                "provider": "openai",
                "model_id": "old-model",
                "effort": "medium",
                "execution_mode": "single",
            },
        },
    }


def _capabilities(*, version: str = "1.83.4", effort_passthrough: bool = True):
    return {
        "schema_version": 1,
        "host": "hive",
        "litellm": {
            "repository_pin": {"version": version, "source": "pyproject.toml"},
            "installed_version": version,
            "versions_match": True,
        },
        "transport": {
            "single_call_evidence_ready": True,
        },
        "native_config": {
            "reasoning_effort_passthrough": effort_passthrough,
        },
    }


def _evidence(*, effort: str = "high", version: str = "1.83.4"):
    return {
        "schema_version": 1,
        "transport": "hive_litellm",
        "litellm_version": version,
        "applied_configuration": {
            "provider": "openai",
            "model_id": "new-model",
            "effort": effort,
            "execution_mode": "single",
        },
        "outcome": "partial",
        "response_text": "not copied into gate output",
    }


def test_gate_is_ready_only_for_exact_current_runtime_evidence():
    report = build_hive_promotion_gate(
        _preview(),
        _capabilities(),
        _evidence(),
    )

    assert report["state"] == "runtime_ready_for_manual_hive_edit"
    assert report["ready"] is True
    assert report["blockers"] == []
    assert report["safe_to_auto_apply"] is False
    assert report["automatic_config_mutation"] is False
    assert report["runtime"]["litellm_version"] == "1.83.4"
    assert "response_text" not in str(report)


def test_gate_blocks_when_exact_hive_wire_evidence_is_missing():
    report = build_hive_promotion_gate(
        _preview(),
        _capabilities(),
        None,
    )

    assert report["state"] == "blocked_unproved_runtime"
    assert report["ready"] is False


def test_gate_blocks_mismatched_applied_configuration():
    evidence = _evidence()
    evidence["applied_configuration"]["model_id"] = "different-model"

    report = build_hive_promotion_gate(
        _preview(),
        _capabilities(),
        evidence,
    )

    assert report["state"] == "blocked_evidence_mismatch"
    assert report["ready"] is False
    assert any("does not exactly match" in blocker for blocker in report["blockers"])


def test_gate_blocks_evidence_from_different_litellm_runtime():
    report = build_hive_promotion_gate(
        _preview(),
        _capabilities(version="1.83.4"),
        _evidence(version="1.84.0"),
    )

    assert report["state"] == "blocked_stale_runtime_evidence"
    assert report["ready"] is False
    assert any("different LiteLLM runtime version" in blocker for blocker in report["blockers"])


def test_default_effort_does_not_require_native_effort_passthrough():
    report = build_hive_promotion_gate(
        _preview(effort="default"),
        _capabilities(effort_passthrough=False),
        _evidence(effort="default"),
    )

    assert report["state"] == "runtime_ready_for_manual_hive_edit"
    assert report["ready"] is True
    assert report["runtime"]["reasoning_effort_passthrough_required"] is False
