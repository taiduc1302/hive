from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


class HivePromotionReceiptError(ValueError):
    """Raised when a Hive promotion preview or config cannot be verified."""


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
        raise HivePromotionReceiptError(f"{label} must contain non-empty {key}")
    return value.strip()


def _selected_config(preview: dict[str, Any], key: str) -> dict[str, str]:
    transition = preview.get("selected_transition")
    if not isinstance(transition, dict):
        raise HivePromotionReceiptError("promotion preview must contain selected_transition")
    config = transition.get(key)
    if not isinstance(config, dict):
        raise HivePromotionReceiptError(f"selected_transition must contain {key}")
    return {
        "provider": _required_string(config, "provider", label=key),
        "model_id": _required_string(config, "model_id", label=key),
        "effort": _required_string(config, "effort", label=key),
        "execution_mode": _required_string(config, "execution_mode", label=key),
    }


def _expected_sections(preview: dict[str, Any]) -> list[str]:
    preconditions = preview.get("preconditions")
    if not isinstance(preconditions, dict):
        raise HivePromotionReceiptError("promotion preview must contain preconditions")
    raw = preconditions.get("sections_to_verify")
    if not isinstance(raw, list) or not raw:
        raise HivePromotionReceiptError(
            "preconditions.sections_to_verify must be a non-empty list"
        )
    sections: list[str] = []
    for item in raw:
        if item not in {"llm", "worker_llm"}:
            raise HivePromotionReceiptError(f"unsupported Hive config section: {item!r}")
        if item not in sections:
            sections.append(item)
    return sections


def _expected_patch(
    config: dict[str, str],
    sections: list[str],
) -> dict[str, dict[str, Any]]:
    effort: str | None = config["effort"]
    if effort == "default":
        effort = None
    section_patch = {
        "model": config["model_id"],
        "reasoning_effort": effort,
    }
    return {section: dict(section_patch) for section in sections}


def _validate_reviewed_patches(
    preview: dict[str, Any],
    *,
    after: dict[str, str],
    rollback: dict[str, str],
    sections: list[str],
) -> None:
    apply_patch = preview.get("apply_patch")
    rollback_patch = preview.get("rollback_patch")
    if apply_patch != _expected_patch(after, sections):
        raise HivePromotionReceiptError(
            "apply_patch must exactly match the reviewed after configuration "
            "for the verified Hive sections"
        )
    if rollback_patch != _expected_patch(rollback, sections):
        raise HivePromotionReceiptError(
            "rollback_patch must exactly match the reviewed rollback configuration "
            "for the verified Hive sections"
        )


def _validate_preview(
    preview: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    if preview.get("schema_version") != 1:
        raise HivePromotionReceiptError("promotion preview schema_version must be 1")
    if preview.get("host") != "hive":
        raise HivePromotionReceiptError("promotion preview host must be hive")
    if preview.get("state") != "ready_for_manual_hive_edit":
        raise HivePromotionReceiptError("promotion preview is not ready_for_manual_hive_edit")
    if preview.get("safe_to_auto_apply") is not False:
        raise HivePromotionReceiptError("promotion preview must keep safe_to_auto_apply=false")
    if preview.get("automatic_config_mutation") is not False:
        raise HivePromotionReceiptError(
            "promotion preview must keep automatic_config_mutation=false"
        )
    if preview.get("requires_human_approval") is not True:
        raise HivePromotionReceiptError("promotion preview must require human approval")
    if not isinstance(preview.get("apply_patch"), dict):
        raise HivePromotionReceiptError("promotion preview must contain apply_patch")
    if not isinstance(preview.get("rollback_patch"), dict):
        raise HivePromotionReceiptError("promotion preview must contain rollback_patch")

    before = _selected_config(preview, "before")
    after = _selected_config(preview, "after")
    rollback = _selected_config(preview, "rollback_to")

    if rollback != before:
        raise HivePromotionReceiptError("rollback_to must exactly match before")
    if before["provider"] != after["provider"]:
        raise HivePromotionReceiptError(
            "receipt verification does not support provider changes"
        )
    if before["execution_mode"] != "single" or after["execution_mode"] != "single":
        raise HivePromotionReceiptError(
            "receipt verification supports execution_mode=single only"
        )

    sections = _expected_sections(preview)
    _validate_reviewed_patches(
        preview,
        after=after,
        rollback=rollback,
        sections=sections,
    )
    return before, after, sections


def _normalized_section(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {
            "provider": None,
            "model": None,
            "reasoning_effort": "default",
            "reasoning_effort_key_present": False,
        }
    effort_present = "reasoning_effort" in section
    return {
        "provider": section.get("provider"),
        "model": section.get("model"),
        "reasoning_effort": section.get("reasoning_effort") if effort_present else "default",
        "reasoning_effort_key_present": effort_present,
    }


def _target_matches(
    actual: dict[str, Any],
    expected: dict[str, str],
) -> tuple[bool, list[str]]:
    diffs: list[str] = []

    if actual["provider"] != expected["provider"]:
        diffs.append(
            f"provider expected {expected['provider']!r}, found {actual['provider']!r}"
        )
    if actual["model"] != expected["model_id"]:
        diffs.append(
            f"model expected {expected['model_id']!r}, found {actual['model']!r}"
        )

    expected_effort = expected["effort"]
    if expected_effort == "default":
        if actual["reasoning_effort_key_present"]:
            diffs.append("reasoning_effort should be absent to restore provider default")
    elif actual["reasoning_effort"] != expected_effort:
        diffs.append(
            f"reasoning_effort expected {expected_effort!r}, "
            f"found {actual['reasoning_effort']!r}"
        )

    return not diffs, diffs


def build_hive_promotion_receipt(
    preview: dict[str, Any],
    current_config: dict[str, Any],
) -> dict[str, Any]:
    """Verify current Hive config against a reviewed promotion preview.

    The verifier is read-only and emits only non-secret route observations.
    """
    if not isinstance(preview, dict):
        raise HivePromotionReceiptError("promotion preview must be a JSON object")
    if not isinstance(current_config, dict):
        raise HivePromotionReceiptError("Hive configuration must be a JSON object")

    preview_sha = _canonical_sha256(preview)
    config_sha = _canonical_sha256(current_config)

    try:
        before, after, sections = _validate_preview(preview)
    except HivePromotionReceiptError as exc:
        return {
            "schema_version": 1,
            "host": "hive",
            "state": "blocked_invalid_preview",
            "reason": str(exc),
            "safe_to_auto_mutate": False,
            "automatic_config_mutation": False,
            "verified_change_id": preview.get("change_id"),
            "preview_sha256": preview_sha,
            "current_config_sha256": config_sha,
            "sections": [],
        }

    observations: list[dict[str, Any]] = []
    after_matches = 0
    before_matches = 0

    for section_name in sections:
        actual = _normalized_section(current_config.get(section_name))
        matches_after, after_diffs = _target_matches(actual, after)
        matches_before, before_diffs = _target_matches(actual, before)

        if matches_after:
            section_state = "after"
            after_matches += 1
        elif matches_before:
            section_state = "before"
            before_matches += 1
        else:
            section_state = "drifted"

        observations.append(
            {
                "section": section_name,
                "state": section_state,
                "actual": {
                    "provider": actual["provider"],
                    "model": actual["model"],
                    "reasoning_effort": actual["reasoning_effort"],
                    "reasoning_effort_key_present": actual[
                        "reasoning_effort_key_present"
                    ],
                },
                "after_differences": after_diffs,
                "before_differences": before_diffs,
            }
        )

    if after_matches == len(sections):
        state = "applied_exactly"
        reason = (
            "Every reviewed Hive section exactly matches the approved after configuration."
        )
    elif before_matches == len(sections):
        state = "not_applied"
        reason = (
            "Every reviewed Hive section still matches the approved before configuration."
        )
    else:
        state = "drifted"
        reason = (
            "Hive configuration does not exactly match either the complete reviewed before "
            "state or the complete reviewed after state."
        )

    receipt_material = {
        "change_id": preview.get("change_id"),
        "category": preview.get("category"),
        "scope": preview.get("scope"),
        "preview_sha256": preview_sha,
        "current_config_sha256": config_sha,
        "state": state,
        "sections": observations,
    }

    return {
        "schema_version": 1,
        "host": "hive",
        "state": state,
        "reason": reason,
        "category": preview.get("category"),
        "scope": preview.get("scope"),
        "verified_change_id": preview.get("change_id"),
        "safe_to_auto_mutate": False,
        "automatic_config_mutation": False,
        "requires_human_review": True,
        "preview_sha256": preview_sha,
        "current_config_sha256": config_sha,
        "receipt_sha256": _canonical_sha256(receipt_material),
        "expected_before": before,
        "expected_after": after,
        "sections": observations,
        "privacy": (
            "Receipt excludes credentials, API keys, API bases, and unrelated Hive config. "
            "Only provider/model/reasoning-effort observations are emitted."
        ),
    }


def render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Verification Receipt",
        "",
        f"- State: **{receipt['state']}**",
        f"- Category: **{receipt.get('category') or '—'}**",
        f"- Scope: **{receipt.get('scope') or '—'}**",
        f"- Change ID: `{receipt.get('verified_change_id') or '—'}`",
        "- Automatic config mutation: **disabled**",
        "",
        receipt["reason"],
        "",
        f"- Preview SHA-256: `{receipt['preview_sha256']}`",
        f"- Hive config SHA-256: `{receipt['current_config_sha256']}`",
    ]

    if receipt.get("receipt_sha256"):
        lines.append(f"- Receipt SHA-256: `{receipt['receipt_sha256']}`")

    sections = receipt.get("sections")
    if isinstance(sections, list) and sections:
        lines.extend(["", "## Verified sections", ""])
        for item in sections:
            lines.append(f"### {item['section']}")
            lines.append(f"- State: **{item['state']}**")
            actual = item.get("actual", {})
            lines.append(f"- Provider: `{actual.get('provider')}`")
            lines.append(f"- Model: `{actual.get('model')}`")
            lines.append(
                f"- Reasoning effort: `{actual.get('reasoning_effort')}`"
            )
            differences = item.get("after_differences") or []
            if differences:
                lines.append("- Differences from approved after state:")
                lines.extend(f"  - {difference}" for difference in differences)

    lines.extend(
        [
            "",
            "This receipt is verification-only. It does not modify Hive configuration.",
        ]
    )
    return chr(10).join(lines) + chr(10)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionReceiptError(f"{label} must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a manually applied Hive promotion against the reviewed "
            "non-mutating promotion preview."
        )
    )
    parser.add_argument("--promotion-preview", required=True)
    parser.add_argument("--hive-config", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    receipt = build_hive_promotion_receipt(
        _load_json_object(args.promotion_preview, "promotion preview"),
        _load_json_object(args.hive_config, "Hive configuration"),
    )
    text = (
        json.dumps(receipt, indent=2, ensure_ascii=False) + chr(10)
        if args.json
        else render_markdown(receipt)
    )

    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
