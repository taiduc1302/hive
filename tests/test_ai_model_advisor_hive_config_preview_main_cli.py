from __future__ import annotations

from tools.ai_model_advisor import cli


def test_central_cli_forwards_hive_config_preview(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_preview_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "hive_config_preview_main", fake_preview_main)

    args = cli.build_parser().parse_args(
        [
            "hive-config-preview",
            "--recommendation",
            "recommendation.json",
            "--index",
            "2",
            "--scope",
            "both",
            "--json",
            "--output",
            "preview.json",
        ]
    )

    assert args.func(args) == 0
    assert captured["argv"] == [
        "--recommendation",
        "recommendation.json",
        "--index",
        "2",
        "--scope",
        "both",
        "--json",
        "--output",
        "preview.json",
    ]
