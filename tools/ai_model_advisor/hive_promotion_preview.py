from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

_SCOPES = ("queen", "worker", "both")


class HivePromotionPreviewError(ValueError):
    """Raised when a promotion review cannot be selected for Hive preview."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HivePromotionPreviewError(f"promotion configuration must contain non-empty {key}")
    return value.strip()


def _config_key(config: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _required_string(config, "provider"),
        _required_string(config, "model_id"),
        _required_string(config, "effort"),
        _required_string(config, "execution_mode"),
    )


def _compact_config(config: dict[str, Any]) -> dict[str, str]:
    provider, model_id, effort, execution_mode = _config_key(config)
    return {
        "provider": provider,
        "model_id": model_id,
        "effort": effort,
        "execution_mode": execution_mode,
    }


def _effort_value(config: dict[str, Any]) -> str | None:
    effort = _required_string(config, "effort")
    return None if effort == "default" else effort


def _section_patch(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": _required_string(config, "model_id"),
        "reasoning_effort": _effort_value(config),
    }


def _scoped_patch(config: dict[str, Any], scope: str) -> dict[str, Any]:
    section = _section_patch(config)
    patch: dict[str, Any] = {}
    if scope in {"queen", "both"}:
        patch["llm"] = dict(section)
    if scope in {"worker", "both"}:
        patch["worker_llm"] = dict(section)
    return patch


def _select_review(report: dict[str, Any], category: str) -> dict[str, Any]:
    reviews = report.get("reviews")
    if not isinstance(reviews, list):
        raise HivePromotionPreviewError("promotion review must contain a reviews list")
    matches = [
        item
        for item in reviews
        if isinstance(item, dict) and str(item.get("category", "")).strip() == category
    ]
    if not matches:
        raise HivePromotionPreviewError(f"promotion review has no category {category!r}")
    if len(matches) > 1:
        raise HivePromotionPreviewError(f"promotion review contains duplicate category {category!r}")
    return matches[0]


def _blocked(
    base: dict[str, Any],
    state: str,
    reason: str,
) -> dict[str, Any]:
    return {
        **base,
        "state": state,
        "reason": reason,
        "apply_patch": None,
        "rollback_patch": None,
    }


def build_hive_promotion_preview(
    report: dict[str, Any],
    *,
    category: str,
    scope: str = "queen",
) -> dict[str, Any]:
    """Translate a reviewed promotion into non-mutating Hive apply/rollback previews."""
    if scope not in _SCOPES:
        raise HivePromotionPreviewError(f"scope must be one of: {', '.join(_SCOPES)}")
    category = category.strip()
    if not category:
        raise HivePromotionPreviewError("category must be non-empty")

    item = _select_review(report, category)
    change = item.get("manual_change")
    change_id = change.get("change_id") if isinstance(change, dict) else None
    base: dict[str, Any] = {
        "schema_version": 1,
        "host": "hive",
        "category": category,
        "scope": scope,
        "change_id": change_id,
        "promotion_review_sha256": _canonical_sha256(report),
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "requires_human_approval": True,
        "merge_patch_semantics": (
            "RFC 7396-style preview: null removes an explicit reasoning_effort key "
            "and restores provider default"
        ),
        "preconditions": None,
        "selected_transition": None,
    }

    if report.get("safe_to_apply") is True or report.get("automatic_policy_mutation") is True:
        return _blocked(
            base,
            "blocked_inconsistent_review",
            "Promotion review must remain non-mutating before a Hive config preview can be produced.",
        )
    if (
        item.get("state") != "ready_for_manual_edit"
        or item.get("safe_to_apply") is not False
        or item.get("requires_human_approval") is not True
        or not isinstance(change, dict)
    ):
        return _blocked(
            base,
            "blocked_not_ready",
            "Selected promotion review is not a fail-closed ready_for_manual_edit package.",
        )

    before = change.get("before")
    after = change.get("after")
    rollback = change.get("rollback_to")
    if not all(isinstance(config, dict) for config in (before, after, rollback)):
        return _blocked(
            base,
            "blocked_inconsistent_review",
            "Manual change must contain before, after, and rollback_to configurations.",
        )

    try:
        before_key = _config_key(before)
        after_key = _config_key(after)
        rollback_key = _config_key(rollback)
    except HivePromotionPreviewError as exc:
        return _blocked(base, "blocked_inconsistent_review", str(exc))

    base["selected_transition"] = {
        "before": _compact_config(before),
        "after": _compact_config(after),
        "rollback_to": _compact_config(rollback),
    }

    if rollback_key != before_key:
        return _blocked(
            base,
            "blocked_inconsistent_review",
            "Rollback configuration must exactly match the reviewed before configuration.",
        )

    before_provider, _, _, before_mode = before_key
    after_provider, _, _, after_mode = after_key
    rollback_provider, _, _, rollback_mode = rollback_key
    if len({before_provider, after_provider, rollback_provider}) != 1:
        return _blocked(
            base,
            "blocked_provider_change",
            "Hive promotion preview does not change providers because credentials and API-base "
            "requirements are outside this review package.",
        )
    if any(mode != "single" for mode in (before_mode, after_mode, rollback_mode)):
        return _blocked(
            base,
            "blocked_execution_mode",
            "Hive configuration preview can represent model/reasoning settings only; "
            "non-single orchestration changes require a proven host-specific control.",
        )

    sections = []
    if scope in {"queen", "both"}:
        sections.append("llm")
    if scope in {"worker", "both"}:
        sections.append("worker_llm")
    base["preconditions"] = {
        "expected_provider": before_provider,
        "expected_current": _compact_config(before),
        "sections_to_verify": sections,
        "operator_must_verify_current_config": True,
    }

    return {
        **base,
        "state": "ready_for_manual_hive_edit",
        "reason": (
            "The reviewed transition is representable as a same-provider single-mode Hive "
            "configuration preview. Human verification and editing are still required."
        ),
        "apply_patch": _scoped_patch(after, scope),
        "rollback_patch": _scoped_patch(rollback, scope),
    }


def render_markdown(preview: dict[str, Any]) -> str:
    lines = [
        "# Hive Promotion Config Preview",
        "",
        f"- Category: **{preview['category']}**",
        f"- State: **{preview['state']}**",
        f"- Scope: **{preview['scope']}**",
        f"- Change ID: `{preview.get('change_id') or '—'}`",
        "- Auto-apply: **disabled**",
        "",
        preview["reason"],
    ]
    if preview.get("apply_patch") is not None:
        lines.extend(
            [
                "",
                "## Apply merge patch",
                "",
                "```json",
                json.dumps(preview["apply_patch"], indent=2, ensure_ascii=False),
                "```",
                "",
                "## Rollback merge patch",
                "",
                "```json",
                json.dumps(preview["rollback_patch"], indent=2, ensure_ascii=False),
                "```",
                "",
                "Verify the current Hive config still matches the recorded precondition before "
                "performing any manual edit.",
            ]
        )
    return "\n".join(lines) + "\n"


def _load_report(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionPreviewError("promotion review JSON must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview a reviewed AI Model Advisor promotion as a non-mutating Hive config patch."
    )
    parser.add_argument("--promotion-review", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--scope", choices=_SCOPES, default="queen")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    preview = build_hive_promotion_preview(
        _load_report(args.promotion_review),
        category=args.category,
        scope=args.scope,
    )
    text = (
        json.dumps(preview, indent=2, ensure_ascii=False) + "\n"
        if args.json
        else render_markdown(preview)
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
