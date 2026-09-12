from __future__ import annotations

import json

from tools.ai_model_advisor import sources as source_module
from tools.ai_model_advisor.registry import ModelRegistry
from tools.ai_model_advisor.sources import (
    ScanReport,
    SourceResult,
    baseline_from_report,
    scan_markdown,
    scan_official_sources,
)


def _registry(tmp_path, source_ids=("test_source",)) -> ModelRegistry:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "as_of": "2026-09-12",
                "official_sources": [
                    {"id": source_id, "url": f"https://example.test/{source_id}"}
                    for source_id in source_ids
                ],
                "models": [
                    {
                        "provider": "anthropic",
                        "model_id": "claude-sonnet-5",
                        "label": "Claude Sonnet 5",
                        "source_ids": [source_ids[0]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ModelRegistry(registry_path)


def _write_baseline(tmp_path, report, previous=None):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(baseline_from_report(report, previous), indent=2),
        encoding="utf-8",
    )
    return path


def test_first_signal_baseline_does_not_alert_on_historical_unknowns(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    monkeypatch.setattr(source_module, "_fetch_text", lambda _url: "Claude Opus 4.8")

    report = scan_official_sources(registry)

    assert report.unknown_signals == ["Claude Opus 4.8"]
    assert report.new_unknown_signals == []
    assert report.signal_baseline_ready is False
    assert report.unbaselined_sources == ["test_source"]
    markdown = scan_markdown(report)
    assert "New unregistered model signals since baseline: **0**" in markdown
    assert "## New model signals to review" not in markdown


def test_only_new_unknown_signal_after_baseline_is_promoted(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    monkeypatch.setattr(source_module, "_fetch_text", lambda _url: "Claude Opus 4.8")
    first = scan_official_sources(registry)
    first_baseline = baseline_from_report(first)
    baseline_path = _write_baseline(tmp_path, first)

    monkeypatch.setattr(
        source_module,
        "_fetch_text",
        lambda _url: "Claude Opus 4.8 Claude Fable 9",
    )
    second = scan_official_sources(registry, baseline_path)

    assert second.signal_baseline_ready is True
    assert second.unknown_signals == ["Claude Fable 9", "Claude Opus 4.8"]
    assert second.new_unknown_signals == ["Claude Fable 9"]

    second_baseline_path = _write_baseline(tmp_path, second, first_baseline)
    third = scan_official_sources(registry, second_baseline_path)
    assert third.new_unknown_signals == []


def test_new_source_initializes_without_flooding_alerts(tmp_path, monkeypatch):
    first_registry = _registry(tmp_path, ("source_a",))
    monkeypatch.setattr(source_module, "_fetch_text", lambda _url: "Claude Sonnet 5")
    first = scan_official_sources(first_registry)
    baseline_path = _write_baseline(tmp_path, first)

    second_registry = _registry(tmp_path, ("source_a", "source_b"))

    def fake_fetch(url: str) -> str:
        return "Claude Opus 4.8" if url.endswith("source_b") else "Claude Sonnet 5"

    monkeypatch.setattr(source_module, "_fetch_text", fake_fetch)
    second = scan_official_sources(second_registry, baseline_path)

    assert "Claude Opus 4.8" in second.unknown_signals
    assert second.new_unknown_signals == []
    assert second.unbaselined_sources == ["source_b"]


def test_failed_source_preserves_hash_and_signal_history_while_removed_sources_prune():
    report = ScanReport(
        generated_at="2026-09-12T00:00:00+00:00",
        registry_as_of="2026-09-12",
        changed_sources=[],
        unknown_signals=[],
        results=[
            SourceResult(
                source_id="kept_source",
                url="https://example.test/models",
                ok=False,
                signal_hash="",
                signals=[],
                error="temporary network failure",
            )
        ],
    )
    baseline = baseline_from_report(
        report,
        {
            "source_hashes": {
                "kept_source": "previous-hash",
                "removed_source": "stale-hash",
            },
            "source_signals": {
                "kept_source": ["Claude Opus 4.8"],
                "removed_source": ["GPT-4.1"],
            },
        },
    )

    assert baseline["source_hashes"] == {"kept_source": "previous-hash"}
    assert baseline["source_signals"] == {"kept_source": ["Claude Opus 4.8"]}


def test_automated_openai_release_source_avoids_help_center_blocking():
    registry = ModelRegistry()
    urls = {source_id: source["url"] for source_id, source in registry.sources.items()}

    assert urls["openai_release_notes"] == "https://openai.com/products/release-notes/"
    assert all("help.openai.com" not in str(url) for url in urls.values())
