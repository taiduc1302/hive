from __future__ import annotations

from tools.ai_model_advisor.runtime_capabilities import (
    build_capability_report,
    detect_litellm_pin,
    render_markdown,
)


def test_detect_litellm_pin_from_pyproject(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["litellm==1.83.4", "pytest"]\n',
        encoding="utf-8",
    )

    assert detect_litellm_pin(tmp_path) == {
        "version": "1.83.4",
        "source": "pyproject.toml",
    }


def test_capability_probe_is_offline_and_marks_single_ready(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("litellm==1.83.4\n", encoding="utf-8")

    report = build_capability_report(
        tmp_path,
        installed_version_getter=lambda: "1.83.4",
        transport_probe=lambda _root: {
            "importable": True,
            "provider_class": True,
            "post_transform_capture": True,
        },
        config_probe=lambda _root: {
            "importable": True,
            "reasoning_effort_passthrough": True,
            "config_keys": [
                "llm.reasoning_effort",
                "worker_llm.reasoning_effort",
            ],
        },
    )

    assert report["litellm"]["versions_match"] is True
    assert report["transport"]["single_call_evidence_ready"] is True
    assert report["native_config"]["reasoning_effort_passthrough"] is True
    assert report["controls"]["reasoning_effort"]["native_hive_config_passthrough"] is True
    assert report["controls"]["reasoning_effort"]["config_keys"] == [
        "llm.reasoning_effort",
        "worker_llm.reasoning_effort",
    ]
    assert report["controls"]["execution_modes"]["single"] == "supported_by_advisor_adapter"
    assert (
        report["controls"]["execution_modes"]["subagents"]
        == "host_exists_adapter_not_implemented"
    )
    assert report["evidence_policy"]["network_calls_performed"] is False
    assert report["evidence_policy"]["credentials_required"] is False
    assert report["evidence_policy"]["registry_listing_implies_runtime_support"] is False


def test_capability_probe_warns_on_runtime_pin_mismatch(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("litellm==1.83.4\n", encoding="utf-8")

    report = build_capability_report(
        tmp_path,
        installed_version_getter=lambda: "1.90.0",
        transport_probe=lambda _root: {
            "importable": True,
            "provider_class": True,
            "post_transform_capture": True,
        },
        config_probe=lambda _root: {
            "importable": True,
            "reasoning_effort_passthrough": True,
            "config_keys": [],
        },
    )

    assert report["litellm"]["versions_match"] is False
    assert any("differs from repository pin" in warning for warning in report["warnings"])


def test_capability_probe_fails_closed_without_wire_capture(tmp_path) -> None:
    report = build_capability_report(
        tmp_path,
        installed_version_getter=lambda: None,
        transport_probe=lambda _root: {
            "importable": True,
            "provider_class": True,
            "post_transform_capture": False,
        },
        config_probe=lambda _root: {
            "importable": True,
            "reasoning_effort_passthrough": False,
            "config_keys": [],
        },
    )

    assert report["transport"]["single_call_evidence_ready"] is False
    assert report["evidence_policy"]["unsupported_or_unproved_configuration"] == (
        "fail_closed_no_model_evidence"
    )
    assert any("post-transform request capture" in warning for warning in report["warnings"])
    assert any("configuration.json reasoning_effort passthrough" in warning for warning in report["warnings"])


def test_runtime_capability_markdown_surfaces_evidence_boundary(tmp_path) -> None:
    report = build_capability_report(
        tmp_path,
        installed_version_getter=lambda: "1.83.4",
        transport_probe=lambda _root: {
            "importable": True,
            "provider_class": True,
            "post_transform_capture": True,
        },
        config_probe=lambda _root: {
            "importable": True,
            "reasoning_effort_passthrough": True,
            "config_keys": [
                "llm.reasoning_effort",
                "worker_llm.reasoning_effort",
            ],
        },
    )

    markdown = render_markdown(report)
    assert "Hive Runtime Capability Probe" in markdown
    assert "performs no provider/network call" in markdown
    assert "Native config reasoning-effort passthrough: **True**" in markdown
    assert "`chatgpt_work`: external_host_not_hive" in markdown
