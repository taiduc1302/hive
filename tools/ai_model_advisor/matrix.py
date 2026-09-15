from __future__ import annotations

from typing import Any

from .models import WorkloadProfile
from .recommend import RecommendationEngine


def build_routing_matrix(
    profiles: dict[str, WorkloadProfile],
    engine: RecommendationEngine,
    providers: list[str] | None = None,
    include_limited: bool = False,
    alternatives: int = 2,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        profiles.items(),
        key=lambda item: (-item[1].activity_count, item[0]),
    )
    for category, profile in ordered:
        recommendations = engine.recommend(
            profile,
            providers=providers,
            include_limited=include_limited,
            top_n=max(1, alternatives + 1),
        )
        if not recommendations:
            continue
        rows.append(
            {
                "category": category,
                "activity_count": profile.activity_count,
                "workload": profile.as_dict(),
                "primary": recommendations[0].as_dict(),
                "alternatives": [item.as_dict() for item in recommendations[1 : alternatives + 1]],
            }
        )
    return rows


def _config_label(rec: dict[str, Any]) -> str:
    return f"{rec['label']} / {rec['effort']} / {rec['execution_mode']}"


def routing_matrix_markdown(rows: list[dict[str, Any]], registry_as_of: str) -> str:
    lines = [
        "# AI Model Routing Matrix",
        "",
        f"Registry verified as of: **{registry_as_of}**",
        "",
        "| Task category | Samples | Primary | Alternative 1 | Alternative 2 |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        alternatives = row["alternatives"]
        alt_one = _config_label(alternatives[0]) if alternatives else "—"
        alt_two = _config_label(alternatives[1]) if len(alternatives) > 1 else "—"
        lines.append(
            f"| {row['category']} | {row['activity_count']} | "
            f"{_config_label(row['primary'])} | {alt_one} | {alt_two} |"
        )

    lines.extend(["", "## Category details", ""])
    for row in rows:
        primary = row["primary"]
        lines.extend(
            [
                f"### {row['category']}",
                "",
                f"- Primary: **{_config_label(primary)}**",
                f"- Confidence: **{primary['confidence']:.0%}**",
                "- Why: " + "; ".join(primary["reasons"]),
            ]
        )
        if primary["tradeoffs"]:
            lines.append("- Trade-offs: " + "; ".join(primary["tradeoffs"]))
        lines.append(
            "- Switch up when: the primary configuration repeatedly fails, the task broadens materially, "
            "or error cost rises enough to justify more reasoning/orchestration."
        )
        lines.append("")

    if not rows:
        lines.extend(
            [
                "No recognized task categories were found in the supplied activity.",
                "",
            ]
        )
    return "\n".join(lines)
