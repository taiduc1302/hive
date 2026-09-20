from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_SCOPES = ("queen", "worker", "both")


class HiveConfigPreviewError(ValueError):
    """Raised when a recommendation cannot be represented as a safe Hive config patch."""


def _load_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HiveConfigPreviewError("recommendation JSON must be an object")
    return payload


def _select_recommendation(payload: dict[str, Any], index: int) -> dict[str, Any]:
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        raise HiveConfigPreviewError("recommendation JSON must contain a non-empty recommendations list")
    if index < 0 or index >= len(recommendations):
        raise HiveConfigPreviewError(f"recommendation index {index} is out of range for {len(recommendations)} recommendations")
    recommendation = recommendations[index]
    if not isinstance(recommendation, dict):
        raise HiveConfigPreviewError("selected recommendation must be an object")
    return recommendation


def _effort_value(recommendation: dict[str, Any]) -> str | None:
    effort = recommendation.get("effort")
    if not isinstance(effort, str) or not effort.strip():
        raise HiveConfigPreviewError("selected recommendation must contain a non-empty effort")
    normalized = effort.strip()
    return None if normalized == "default" else normalized


def build_hive_config_preview(
    payload: dict[str, Any],
    *,
    scope: str = "queen",
    index: int = 0,
) -> dict[str, Any]:
    """Build a non-mutating merge-patch preview for native Hive reasoning effort."""
    if scope not in _SCOPES:
        raise HiveConfigPreviewError(f"scope must be one of: {', '.join(_SCOPES)}")

    recommendation = _select_recommendation(payload, index)
    effort = _effort_value(recommendation)
    patch: dict[str, Any] = {}
    if scope in {"queen", "both"}:
        patch["llm"] = {"reasoning_effort": effort}
    if scope in {"worker", "both"}:
        patch["worker_llm"] = {"reasoning_effort": effort}

    execution_mode = str(recommendation.get("execution_mode") or "")
    warnings: list[str] = []
    if execution_mode and execution_mode != "single":
        warnings.append(f"recommended execution_mode={execution_mode!r} is not applied by this patch; this preview changes reasoning effort only")

    return {
        "schema_version": 1,
        "host": "hive",
        "scope": scope,
        "recommendation_index": index,
        "selected_configuration": {
            "provider": recommendation.get("provider"),
            "model_id": recommendation.get("model_id"),
            "effort": recommendation.get("effort"),
            "execution_mode": recommendation.get("execution_mode"),
        },
        "merge_patch": patch,
        "merge_patch_semantics": ("RFC 7396-style preview: null removes an explicit reasoning_effort key and restores provider default"),
        "safe_to_auto_apply": False,
        "warnings": warnings,
    }


def render_markdown(preview: dict[str, Any]) -> str:
    config = preview["selected_configuration"]
    patch_text = json.dumps(preview["merge_patch"], indent=2, ensure_ascii=False)
    lines = [
        "# Hive Config Preview",
        "",
        f"- Scope: **{preview['scope']}**",
        f"- Model: `{config.get('provider')} / {config.get('model_id')}`",
        f"- Effort: `{config.get('effort')}`",
        f"- Execution mode: `{config.get('execution_mode')}`",
        "- Auto-apply: **disabled**",
        "",
        "## Merge patch",
        "",
        "```json",
        patch_text,
        "```",
        "",
        "This preview changes reasoning effort only. It does not change credentials, provider, model, or orchestration.",
    ]
    warnings = preview.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview the native Hive reasoning-effort config patch for an Advisor recommendation.")
    parser.add_argument("--recommendation", required=True, help="Advisor recommendation JSON")
    parser.add_argument("--index", type=int, default=0, help="Recommendation index (default: 0)")
    parser.add_argument("--scope", choices=_SCOPES, default="queen")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of Markdown")
    parser.add_argument("--output", help="Optional output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    preview = build_hive_config_preview(
        _load_payload(args.recommendation),
        scope=args.scope,
        index=args.index,
    )
    text = json.dumps(preview, indent=2, ensure_ascii=False) + "\n" if args.json else render_markdown(preview)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
