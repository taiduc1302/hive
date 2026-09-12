from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "registry_as_of": self.registry_as_of,
            "changed_sources": self.changed_sources,
            "unknown_signals": self.unknown_signals,
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


def scan_official_sources(
    registry: ModelRegistry,
    baseline_path: str | Path | None = None,
) -> ScanReport:
    baseline = load_baseline(baseline_path)
    baseline_hashes = baseline.get("source_hashes", {}) if isinstance(baseline, dict) else {}

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
    model_prefixes = ("claude ", "claude-", "gpt", "gpt-")
    unknown = [
        signal
        for signal in sorted(all_signals, key=str.lower)
        if signal.lower().startswith(model_prefixes) and signal.lower() not in known
    ]
    return ScanReport(
        datetime.now(UTC).isoformat(timespec="seconds"),
        registry.as_of,
        changed,
        unknown,
        results,
    )


def baseline_from_report(
    report: ScanReport,
    previous_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_hashes = (previous_baseline or {}).get("source_hashes", {})
    source_hashes = dict(previous_hashes) if isinstance(previous_hashes, dict) else {}
    source_hashes.update(
        {item.source_id: item.signal_hash for item in report.results if item.ok}
    )
    return {
        "generated_at": report.generated_at,
        "source_hashes": source_hashes,
    }


def scan_markdown(report: ScanReport) -> str:
    lines = [
        "# Official AI source scan",
        "",
        f"Generated: {report.generated_at}",
        f"Registry as-of: **{report.registry_as_of}**",
        "",
        f"Changed source fingerprints: **{len(report.changed_sources)}**",
        f"Potential unknown model signals: **{len(report.unknown_signals)}**",
        "",
    ]
    if report.changed_sources:
        lines.extend(
            [
                "## Sources with changed model/mode signals",
                "",
                *[f"- `{item}`" for item in report.changed_sources],
                "",
            ]
        )
    if report.unknown_signals:
        lines.extend(
            [
                "## Signals to review",
                "",
                *[f"- `{item}`" for item in report.unknown_signals[:50]],
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
                "The scanner intentionally uses official sources only by default."
            ),
            "",
        ]
    )
    return "\n".join(lines)
