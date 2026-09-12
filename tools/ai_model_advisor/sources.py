from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .registry import ModelRegistry

SIGNAL_RE = re.compile(
    r"(Claude\s+(?:Fable|Mythos|Opus|Sonnet|Haiku)\s+\d+(?:\.\d+)?"
    r"|claude-(?:fable|mythos|opus|sonnet|haiku)-\d+(?:-\d+)?(?:-\d{8})?"
    r"|GPT[-‑ ]?\d+(?:\.\d+)?(?:\s+(?:Sol|Terra|Luna|Astra|Pro|Codex))?"
    r"|gpt-\d+(?:\.\d+)?(?:-(?:sol|terra|luna|astra|pro|codex))?"
    r"|\b(?:ultracode|xhigh|max effort|dynamic workflow|scheduled tasks?|skills?)\b)",
    flags=re.IGNORECASE,
)
_MODEL_SIGNAL_PREFIXES = ("claude ", "claude-", "gpt", "gpt-")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


@dataclass
class SourceResult:
    source_id: str
    url: str
    ok: bool
    signal_hash: str
    signals: list[str]
    error: str = ""


@dataclass
class ScanReport:
    generated_at: str
    registry_as_of: str
    changed_sources: list[str]
    unknown_signals: list[str]
    results: list[SourceResult]
    new_unknown_signals: list[str] = field(default_factory=list)
    signal_baseline_ready: bool = False
    unbaselined_sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "registry_as_of": self.registry_as_of,
            "changed_sources": self.changed_sources,
            "unknown_signals": self.unknown_signals,
            "new_unknown_signals": self.new_unknown_signals,
            "signal_baseline_ready": self.signal_baseline_ready,
            "unbaselined_sources": self.unbaselined_sources,
            "results": [asdict(item) for item in self.results],
        }


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "hive-ai-model-advisor/0.1 (+https://github.com/taiduc1302/hive)"
        },
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(raw)
    return html.unescape("\n".join(parser.parts))


def _signals(text: str) -> list[str]:
    values = {" ".join(match.group(0).split()) for match in SIGNAL_RE.finditer(text)}
    return sorted(values, key=str.lower)


def _signal_hash(signals: list[str]) -> str:
    normalized = "\n".join(item.lower() for item in signals).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def load_baseline(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _known_model_signals(registry: ModelRegistry) -> set[str]:
    known: set[str] = set()
    for model in registry.models:
        model_id = model.model_id.lower()
        known.add(model_id)
        known.add(model.label.lower())
        snapshot = re.fullmatch(r"(.+)-\d{8}", model_id)
        if snapshot:
            known.add(snapshot.group(1))
    return known


def _baseline_source_signals(baseline: dict[str, Any]) -> dict[str, list[str]]:
    raw = baseline.get("source_signals", {})
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for source_id, signals in raw.items():
        if not isinstance(source_id, str) or not isinstance(signals, list):
            continue
        normalized[source_id] = [signal for signal in signals if isinstance(signal, str)]
    return normalized


def _is_unregistered_model_signal(signal: str, known: set[str]) -> bool:
    lowered = signal.lower()
    return lowered.startswith(_MODEL_SIGNAL_PREFIXES) and lowered not in known


def scan_official_sources(
    registry: ModelRegistry,
    baseline_path: str | Path | None = None,
) -> ScanReport:
    baseline = load_baseline(baseline_path)
    baseline_hashes = baseline.get("source_hashes", {}) if isinstance(baseline, dict) else {}
    previous_source_signals = _baseline_source_signals(baseline)

    results: list[SourceResult] = []
    changed: list[str] = []
    all_signals: set[str] = set()
    for source_id, source in registry.sources.items():
        url = str(source["url"])
        try:
            signals = _signals(_fetch_text(url))
            digest = _signal_hash(signals)
            result = SourceResult(source_id, url, True, digest, signals)
            if baseline_hashes.get(source_id) and baseline_hashes[source_id] != digest:
                changed.append(source_id)
            all_signals.update(signals)
        except Exception as exc:
            result = SourceResult(
                source_id,
                url,
                False,
                "",
                [],
                f"{type(exc).__name__}: {exc}",
            )
        results.append(result)

    known = _known_model_signals(registry)
    unknown = [
        signal
        for signal in sorted(all_signals, key=str.lower)
        if _is_unregistered_model_signal(signal, known)
    ]

    previous_all = {
        signal.lower()
        for signals in previous_source_signals.values()
        for signal in signals
    }
    new_by_lower: dict[str, str] = {}
    for item in results:
        previous = previous_source_signals.get(item.source_id)
        if not item.ok or previous is None:
            continue
        previous_for_source = {signal.lower() for signal in previous}
        for signal in item.signals:
            lowered = signal.lower()
            if not _is_unregistered_model_signal(signal, known):
                continue
            if lowered in previous_for_source or lowered in previous_all:
                continue
            new_by_lower.setdefault(lowered, signal)

    unbaselined_sources = [
        item.source_id
        for item in results
        if item.ok and item.source_id not in previous_source_signals
    ]
    signal_baseline_ready = any(
        source_id in previous_source_signals for source_id in registry.sources
    )
    return ScanReport(
        datetime.now(UTC).isoformat(timespec="seconds"),
        registry.as_of,
        changed,
        unknown,
        results,
        new_unknown_signals=sorted(new_by_lower.values(), key=str.lower),
        signal_baseline_ready=signal_baseline_ready,
        unbaselined_sources=unbaselined_sources,
    )


def baseline_from_report(
    report: ScanReport,
    previous_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = previous_baseline or {}
    previous_hashes = previous.get("source_hashes", {})
    if not isinstance(previous_hashes, dict):
        previous_hashes = {}
    previous_signals = _baseline_source_signals(previous)

    source_hashes: dict[str, str] = {}
    source_signals: dict[str, list[str]] = {}
    for item in report.results:
        if item.ok:
            source_hashes[item.source_id] = item.signal_hash
            source_signals[item.source_id] = list(item.signals)
            continue
        previous_hash = previous_hashes.get(item.source_id)
        if isinstance(previous_hash, str):
            source_hashes[item.source_id] = previous_hash
        if item.source_id in previous_signals:
            source_signals[item.source_id] = list(previous_signals[item.source_id])

    return {
        "generated_at": report.generated_at,
        "source_hashes": source_hashes,
        "source_signals": source_signals,
    }


def scan_markdown(report: ScanReport) -> str:
    lines = [
        "# Official AI source scan",
        "",
        f"Generated: {report.generated_at}",
        f"Registry as-of: **{report.registry_as_of}**",
        "",
        f"Changed source fingerprints: **{len(report.changed_sources)}**",
        f"New unregistered model signals since baseline: **{len(report.new_unknown_signals)}**",
        f"Observed unregistered model-like signals: **{len(report.unknown_signals)}**",
        "",
    ]
    if not report.signal_baseline_ready:
        lines.extend(
            [
                "Signal-history baseline is being initialized. Existing unregistered-looking names are informational and are not treated as new on this run.",
                "",
            ]
        )
    elif report.unbaselined_sources:
        lines.extend(
            [
                "The following successful sources have no prior signal-history baseline and are initialized without generating new-model alerts:",
                "",
                *[f"- `{item}`" for item in report.unbaselined_sources],
                "",
            ]
        )
    if report.changed_sources:
        lines.extend(
            [
                "## Sources with changed model/mode signals",
                "",
                *[f"- `{item}`" for item in report.changed_sources],
                "",
            ]
        )
    if report.new_unknown_signals:
        lines.extend(
            [
                "## New model signals to review",
                "",
                *[f"- `{item}`" for item in report.new_unknown_signals[:50]],
                "",
            ]
        )
    lines.extend(
        [
            "## Source health",
            "",
            "| Source | Status | Signals |",
            "|---|---|---:|",
        ]
    )
    for item in report.results:
        status = "OK" if item.ok else f"ERROR: {item.error}"
        lines.append(f"| `{item.source_id}` | {status} | {len(item.signals)} |")
    lines.extend(
        [
            "",
            (
                "A changed fingerprint is a review trigger, not proof that a model changed. "
                "Only newly observed unregistered model signals from an already-baselined source are promoted to review alerts. "
                "The scanner intentionally uses official sources only by default."
            ),
            "",
        ]
    )
    return "\n".join(lines)
