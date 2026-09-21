from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .runtime_capabilities import build_capability_report


class HivePromotionGateError(ValueError):
    """Raised when promotion-gate inputs are malformed."""


_CONFIG_KEYS = ("provider", "model_id", "effort", "execution_mode")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_string(mapping: dict[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HivePromotionGateError(f"{label} must contain non-empty {key}")
    return value.strip()


def _after_configuration(preview: dict[str, Any]) -> dict[str, str]:
    transition = preview.get("selected_transition")
    if not isinstance(transition, dict):
        raise HivePromotionGateError("promotion preview must contain selected_transition")
    after = transition.get("after")
    if not isinstance(after, dict):
        raise HivePromotionGateError("selected_transition must contain after")
    return {
        key: _required_string(after, key, label="selected_transition.after")
        for key in _CONFIG_KEYS
    }


def _validate_preview(preview: dict[str, Any]) -> dict[str, str]:
    if preview.get("schema_version") != 1:
        raise HivePromotionGateError("promotion preview schema_version must be 1")
    if preview.get("host") != "hive":
        raise HivePromotionGateError("promotion preview host must be hive")
    if preview.get("state") != "ready_for_manual_hive_edit":
        raise HivePromotionGateError("promotion preview is not ready_for_manual_hive_edit")
    if preview.get("safe_to_auto_apply") is not False:
        raise HivePromotionGateError("promotion preview must keep safe_to_auto_apply=false")
    if preview.get("automatic_config_mutation") is not False:
        raise HivePromotionGateError(
            "promotion preview must keep automatic_config_mutation=false"
        )
    if preview.get("requires_human_approval") is not True:
        raise HivePromotionGateError("promotion preview must require human approval")
    after = _after_configuration(preview)
    if after["execution_mode"] != "single":
        raise HivePromotionGateError(
            "Hive promotion gate supports execution_mode=single only"
        )
    return after


def _blocked(
    base: dict[str, Any],
    state: str,
    reason: str,
    *,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **base,
        "state": state,
        "reason": reason,
        "ready": False,
        "blockers": blockers or [reason],
        "warnings": warnings or [],
    }


def _runtime_version(capabilities: dict[str, Any]) -> str | None:
    value = (capabilities.get("litellm") or {}).get("installed_version")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _runtime_blockers(
    capabilities: dict[str, Any],
    *,
    after: dict[str, str],
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    if capabilities.get("host") != "hive":
        blockers.append("runtime capability report host must be hive")

    transport = capabilities.get("transport")
    if not isinstance(transport, dict) or not transport.get(
        "single_call_evidence_ready"
    ):
        blockers.append(
            "Hive single-call post-transform evidence transport is not ready"
        )

    installed_version = _runtime_version(capabilities)
    if installed_version is None:
        blockers.append("current installed LiteLLM version is not proven")

    native_config = capabilities.get("native_config")
    if after["effort"] != "default" and (
        not isinstance(native_config, dict)
        or not native_config.get("reasoning_effort_passthrough")
    ):
        blockers.append(
            "native Hive reasoning_effort config passthrough is not proven"
        )

    litellm = capabilities.get("litellm")
    if isinstance(litellm, dict) and litellm.get("versions_match") is False:
        warnings.append(
            "Installed LiteLLM differs from the repository pin; exact wire evidence "
            "must come from the installed runtime version."
        )

    return blockers, warnings


def _validate_evidence(
    evidence: dict[str, Any],
    *,
    after: dict[str, str],
    runtime_version: str,
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    if evidence.get("schema_version") != 1:
        blockers.append("Hive evidence schema_version must be 1")
    if evidence.get("transport") != "hive_litellm":
        blockers.append("Hive evidence transport must be hive_litellm")

    applied = evidence.get("applied_configuration")
    if not isinstance(applied, dict):
        blockers.append("Hive evidence must contain applied_configuration")
    else:
        normalized = {key: applied.get(key) for key in _CONFIG_KEYS}
        if normalized != after:
            blockers.append(
                "Hive evidence applied_configuration does not exactly match the "
                "reviewed after configuration"
            )

    evidence_version = evidence.get("litellm_version")
    if not isinstance(evidence_version, str) or not evidence_version.strip():
        blockers.append("Hive evidence must identify litellm_version")
    elif evidence_version.strip() != runtime_version:
        blockers.append(
            "Hive evidence was produced by a different LiteLLM runtime version "
            f"({evidence_version.strip()} != {runtime_version})"
        )

    if evidence.get("outcome") not in {"success", "partial", "failure"}:
        warnings.append(
            "Hive evidence outcome is not a benchmark verdict; only transport/config "
            "proof is used by this gate."
        )

    return blockers, warnings


def build_hive_promotion_gate(
    preview: dict[str, Any],
    capabilities: dict[str, Any],
    hive_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed unless the reviewed Hive transition has fresh runtime proof.

    This gate is read-only. It verifies host plumbing plus exact model/effort
    wire evidence before an operator considers the already-reviewed manual edit.
    """
    if not isinstance(preview, dict):
        raise HivePromotionGateError("promotion preview must be a JSON object")
    if not isinstance(capabilities, dict):
        raise HivePromotionGateError("runtime capabilities must be a JSON object")
    if hive_evidence is not None and not isinstance(hive_evidence, dict):
        raise HivePromotionGateError("Hive evidence must be a JSON object")

    preview_sha = _canonical_sha256(preview)
    capabilities_sha = _canonical_sha256(capabilities)

    try:
        after = _validate_preview(preview)
    except HivePromotionGateError as exc:
        return {
            "schema_version": 1,
            "host": "hive",
            "state": "blocked_invalid_preview",
            "reason": str(exc),
            "ready": False,
            "safe_to_auto_apply": False,
            "automatic_config_mutation": False,
            "requires_human_approval": True,
            "preview_sha256": preview_sha,
            "runtime_capabilities_sha256": capabilities_sha,
            "hive_evidence_sha256": (
                _canonical_sha256(hive_evidence)
                if isinstance(hive_evidence, dict)
                else None
            ),
            "after_configuration": None,
            "blockers": [str(exc)],
            "warnings": [],
        }

    base = {
        "schema_version": 1,
        "host": "hive",
        "category": preview.get("category"),
        "change_id": preview.get("change_id"),
        "scope": preview.get("scope"),
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "requires_human_approval": True,
        "preview_sha256": preview_sha,
        "runtime_capabilities_sha256": capabilities_sha,
        "hive_evidence_sha256": (
            _canonical_sha256(hive_evidence)
            if isinstance(hive_evidence, dict)
            else None
        ),
        "after_configuration": after,
    }

    runtime_blockers, warnings = _runtime_blockers(
        capabilities,
        after=after,
    )
    if runtime_blockers:
        return _blocked(
            base,
            "blocked_runtime_plumbing",
            "Current Hive runtime plumbing is not proven for this promotion.",
            blockers=runtime_blockers,
            warnings=warnings,
        )

    runtime_version = _runtime_version(capabilities)
    assert runtime_version is not None

    if hive_evidence is None:
        return _blocked(
            base,
            "blocked_unproved_runtime",
            "Exact model/effort wire evidence from the current Hive runtime is required.",
            blockers=[
                "Run the candidate through tools.ai_model_advisor.hive_litellm_adapter "
                "and provide its successful transport result."
            ],
            warnings=warnings,
        )

    evidence_blockers, evidence_warnings = _validate_evidence(
        hive_evidence,
        after=after,
        runtime_version=runtime_version,
    )
    warnings.extend(evidence_warnings)
    if evidence_blockers:
        stale = any(
            "different LiteLLM runtime version" in blocker
            for blocker in evidence_blockers
        )
        return _blocked(
            base,
            "blocked_stale_runtime_evidence" if stale else "blocked_evidence_mismatch",
            "Hive runtime evidence does not prove the exact reviewed transition.",
            blockers=evidence_blockers,
            warnings=warnings,
        )

    return {
        **base,
        "state": "runtime_ready_for_manual_hive_edit",
        "reason": (
            "Current Hive plumbing is proven and the exact reviewed model/effort "
            "configuration was verified on the post-transform Hive LiteLLM wire path. "
            "The config edit remains manual."
        ),
        "ready": True,
        "blockers": [],
        "warnings": warnings,
        "runtime": {
            "litellm_version": runtime_version,
            "transport": "hive_litellm",
            "reasoning_effort_passthrough_required": after["effort"] != "default",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Runtime Gate",
        "",
        f"- Category: **{report.get('category') or 'unknown'}**",
        f"- State: **{report['state']}**",
        f"- Runtime ready: **{'yes' if report.get('ready') else 'no'}**",
        "- Auto-apply: **disabled**",
        "",
        report["reason"],
    ]
    after = report.get("after_configuration")
    if isinstance(after, dict):
        lines.extend(
            [
                "",
                "## Reviewed target",
                "",
                f"- Provider: `{after['provider']}`",
                f"- Model: `{after['model_id']}`",
                f"- Effort: `{after['effort']}`",
                f"- Execution mode: `{after['execution_mode']}`",
            ]
        )
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in blockers)
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
    lines.extend(
        [
            "",
            "This gate is evidence only. It never edits Hive configuration.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionGateError(f"{path} must contain a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require current Hive runtime and exact post-transform wire evidence "
            "before a reviewed manual promotion."
        )
    )
    parser.add_argument("--promotion-preview", required=True)
    parser.add_argument(
        "--runtime-capabilities",
        help=(
            "Optional JSON from runtime_capabilities --json. When omitted, probe "
            "the current checkout/runtime directly."
        ),
    )
    parser.add_argument(
        "--hive-evidence",
        help="JSON result produced by hive_litellm_adapter for the reviewed candidate.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 unless exact current-runtime evidence makes the gate ready.",
    )
    args = parser.parse_args(argv)

    preview = _load_json(args.promotion_preview)
    capabilities = (
        _load_json(args.runtime_capabilities)
        if args.runtime_capabilities
        else build_capability_report()
    )
    evidence = _load_json(args.hive_evidence) if args.hive_evidence else None
    report = build_hive_promotion_gate(preview, capabilities, evidence)

    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json
        else render_markdown(report)
    )
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    if args.require_ready and not report.get("ready"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
