from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .models import WorkloadProfile


CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "implementation": ("implement", "feature", "code", "endpoint", "frontend", "backend", "script"),
    "debugging": ("bug", "debug", "error", "trace", "race condition", "failing test", "fix"),
    "architecture": ("architecture", "design", "refactor", "system", "integration", "orchestration"),
    "repo_review": ("repo", "repository", "code review", "audit", "migration", "many files", "whole project"),
    "research": ("research", "compare", "investigate", "latest", "source", "benchmark", "analyze"),
    "automation": ("automation", "agent", "workflow", "scheduled", "cron", "mcp", "bot"),
    "documents": ("document", "docx", "pdf", "report", "email", "proposal"),
    "spreadsheets": ("excel", "xlsx", "spreadsheet", "estimate", "takeoff", "quantity"),
}


def _clamp(value: float) -> float:
    return max(1.0, min(5.0, value))


def _text_from_chatgpt_node(node: dict[str, Any]) -> str:
    message = node.get("message") or {}
    content = message.get("content") or {}
    parts = content.get("parts") or []
    return " ".join(part for part in parts if isinstance(part, str))


class ActivityAnalyzer:
    """Turn activity text into a compact workload profile.

    This intentionally has no private ChatGPT-history API. It accepts an explicit
    ChatGPT data export, generic activity JSON, or GitHub public events.
    """

    def from_texts(self, texts: Iterable[str]) -> WorkloadProfile:
        items = [text.strip() for text in texts if text and text.strip()]
        categories: Counter[str] = Counter()
        dims = Counter({"coding": 0.0, "reasoning": 0.0, "agentic": 0.0, "ambiguity": 0.0, "breadth": 0.0, "parallelism": 0.0})
        evidence: list[str] = []

        for text in items:
            lower = text.lower()
            matched: set[str] = set()
            for category, patterns in CATEGORY_PATTERNS.items():
                if any(pattern in lower for pattern in patterns):
                    categories[category] += 1
                    matched.add(category)
            if matched:
                evidence.append(text[:180].replace("\n", " "))
            if matched & {"implementation", "debugging", "architecture", "repo_review"}:
                dims["coding"] += 1
            if matched & {"debugging", "architecture", "research", "spreadsheets"}:
                dims["reasoning"] += 1
            if matched & {"automation", "repo_review", "architecture"}:
                dims["agentic"] += 1
            if matched & {"research", "architecture", "debugging"}:
                dims["ambiguity"] += 1
            if matched & {"repo_review", "research", "architecture"}:
                dims["breadth"] += 1
            if matched & {"repo_review", "research", "automation"}:
                dims["parallelism"] += 1
            if re.search(r"\b(all|whole|entire|every|hundreds?|dozens?|multi[- ]repo)\b", lower):
                dims["breadth"] += 1.5
                dims["parallelism"] += 1.0
            if re.search(r"\b(unknown|unclear|figure out|root cause|open[- ]ended)\b", lower):
                dims["ambiguity"] += 1.5
            if re.search(r"\b(overnight|autonomous|long[- ]running|many steps)\b", lower):
                dims["agentic"] += 1.5

        count = max(1, len(items))
        scale = max(1.0, count ** 0.5)

        def dimension(name: str, base: float = 1.0) -> float:
            return _clamp(base + (dims[name] / scale))

        return WorkloadProfile(
            coding=dimension("coding"), reasoning=dimension("reasoning"), agentic=dimension("agentic"),
            ambiguity=dimension("ambiguity"), breadth=dimension("breadth"), parallelism=dimension("parallelism"),
            latency_sensitivity=3.0, cost_sensitivity=3.0, volume=_clamp(1.0 + count / 20.0),
            categories=dict(categories), activity_count=len(items), evidence=evidence[:12],
        )

    def from_generic_json(self, path: str | Path) -> WorkloadProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            records = data.get("activities", data.get("items", []))
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError("Activity JSON must be a list or object")
        texts = []
        for record in records:
            if isinstance(record, str):
                texts.append(record)
            elif isinstance(record, dict):
                texts.append(" ".join(str(record.get(k, "")) for k in ("title", "text", "body", "action")))
        return self.from_texts(texts)

    def from_chatgpt_export(self, path: str | Path) -> WorkloadProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        conversations = data if isinstance(data, list) else data.get("conversations", [])
        texts: list[str] = []
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            title = conversation.get("title")
            if isinstance(title, str):
                texts.append(title)
            mapping = conversation.get("mapping") or {}
            if isinstance(mapping, dict):
                for node in mapping.values():
                    if not isinstance(node, dict):
                        continue
                    message = node.get("message") or {}
                    author = (message.get("author") or {}).get("role")
                    if author == "user":
                        text = _text_from_chatgpt_node(node)
                        if text:
                            texts.append(text)
        return self.from_texts(texts)

    def from_github_events(self, events: list[dict[str, Any]]) -> WorkloadProfile:
        texts: list[str] = []
        for event in events:
            repo = (event.get("repo") or {}).get("name", "")
            event_type = event.get("type", "")
            payload = event.get("payload") or {}
            fragments = [str(event_type), str(repo)]
            for key in ("action", "ref_type", "description"):
                if payload.get(key):
                    fragments.append(str(payload[key]))
            commits = payload.get("commits") or []
            for commit in commits[:20]:
                if isinstance(commit, dict) and commit.get("message"):
                    fragments.append(str(commit["message"]))
            texts.append(" ".join(fragments))
        return self.from_texts(texts)

    @staticmethod
    def fetch_github_public_events(username: str, token: str | None = None) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"https://api.github.com/users/{username}/events?per_page=100",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "hive-ai-model-advisor/0.1"},
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Unexpected GitHub events response")
        return data
