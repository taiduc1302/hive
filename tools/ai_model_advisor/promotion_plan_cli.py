from __future__ import annotations

import argparse
import json
from pathlib import Path

from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore
from .promotion_plan import build_promotion_plans, write_promotion_outputs
from .routing_proposals import build_routing_proposals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build review-only promotion/canary plans from empirical routing evidence."
    )
    parser.add_argument("--routing-matrix", required=True)
    parser.add_argument("--feedback", required=True)
    parser.add_argument("--output", help="Optional Markdown promotion plan")
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.routing_matrix).read_text(encoding="utf-8"))
    rows = payload.get("routing_matrix", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("routing matrix JSON must be a list or contain a routing_matrix list")

    leaderboard = build_empirical_leaderboard(FeedbackStore.load(args.feedback))
    proposals = build_routing_proposals(rows, leaderboard)
    report = build_promotion_plans(proposals, leaderboard)
    markdown = write_promotion_outputs(report, args.output, args.json_output)
    if not args.output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
