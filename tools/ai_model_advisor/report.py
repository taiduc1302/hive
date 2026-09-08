from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from .models import Recommendation, WorkloadProfile


def recommendation_markdown(
    workload: WorkloadProfile,
    recommendations: Iterable[Recommendation],
    registry_as_of: str,
) -> str:
    rows = list(recommendations)
    lines = [
        "# AI Model Advisor",
        "",
        f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Registry verified as of: **{registry_as_of}**",
        "",
        "## Workload profile",
        "",
        f"Activities analyzed: **{workload.activity_count}**",
        "",
        "| Dimension | 1-5 |",
        "|---|---:|",
    ]
    for name in (
        "coding",
        "reasoning",
        "agentic",
        "ambiguity",
        "breadth",
        "parallelism",
        "latency_sensitivity",
        "cost_sensitivity",
    ):
        lines.append(f"| {name.replace('_', ' ').title()} | {getattr(workload, name):.1f} |")

    if workload.categories:
        top_categories = sorted(
            workload.categories.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:6]
        category_text = ", ".join(f"{name} ({count})" for name, count in top_categories)
        lines.extend(["", f"Top activity categories: {category_text}"])

    lines.extend(["", "## Recommended configurations", ""])
    for index, rec in enumerate(rows, start=1):
        lines.extend(
            [
                f"### {index}. {rec.label}",
                "",
                f"- Provider: `{rec.provider}`",
                f"- Model: `{rec.model_id}`",
                f"- Effort: **{rec.effort}**",
                f"- Execution: **{rec.execution_mode}**",
                f"- Score: **{rec.score:.2f}**",
                (
                    f"- Score breakdown: base **{rec.base_score:.2f}**; "
                    f"applied empirical **{rec.empirical_adjustment:+.3f}** "
                    f"(quality {rec.quality_adjustment:+.3f}, "
                    f"efficiency {rec.efficiency_adjustment:+.3f})"
                ),
                f"- Confidence: **{rec.confidence:.0%}**",
                "- Why: " + "; ".join(rec.reasons),
            ]
        )
        if rec.raw_empirical_adjustment != rec.empirical_adjustment:
            lines.append(
                f"- Empirical cap: raw {rec.raw_empirical_adjustment:+.3f} was capped to "
                f"{rec.empirical_adjustment:+.3f} before ranking"
            )
        if rec.tradeoffs:
            lines.append("- Trade-offs: " + "; ".join(rec.tradeoffs))
        lines.append("")

    lines.extend(
        [
            "## Interpretation rule",
            "",
            (
                "Do not automatically choose the largest model. Move up in model/effort only when recent tasks "
                "show more ambiguity, breadth, autonomy, or error cost. Use workflow/multi-agent modes only when "
                "the work is genuinely parallelizable or too large for one context."
            ),
            "",
            "## Privacy boundary",
            "",
            (
                "This report only analyzes activity explicitly provided to the tool (for example a ChatGPT export "
                "or GitHub events). It does not have a hidden API to a user's full ChatGPT history."
            ),
        ]
    )
    return "\n".join(lines) + "\n"
