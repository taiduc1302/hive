#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

PATTERNS = {
    "coding": ("code", "implement", "feature", "bug", "debug", "repo", "repository", "refactor"),
    "architecture": ("architecture", "design", "integration", "orchestration", "system"),
    "research": ("research", "latest", "compare", "investigate", "analyze"),
    "automation": ("automation", "agent", "workflow", "scheduled", "mcp", "bot"),
    "broad_scope": ("whole", "entire", "all files", "all repos", "hundreds", "many files"),
}


def chatgpt_texts(data: Any) -> list[str]:
    conversations = data if isinstance(data, list) else data.get("conversations", [])
    texts: list[str] = []
    for conversation in conversations:
        if not isinstance(conversation, dict): continue
        if isinstance(conversation.get("title"), str): texts.append(conversation["title"])
        mapping = conversation.get("mapping") or {}
        if not isinstance(mapping, dict): continue
        for node in mapping.values():
            if not isinstance(node, dict): continue
            message = node.get("message") or {}
            if (message.get("author") or {}).get("role") != "user": continue
            texts.extend(part for part in (message.get("content") or {}).get("parts", []) if isinstance(part, str))
    return texts


def generic_texts(data: Any) -> list[str]:
    records = data.get("activities", data.get("items", [])) if isinstance(data, dict) else data
    texts: list[str] = []
    if not isinstance(records, list): return texts
    for record in records:
        if isinstance(record, str): texts.append(record)
        elif isinstance(record, dict): texts.append(" ".join(str(record.get(key, "")) for key in ("title", "text", "body", "action")))
    return texts


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("input"); parser.add_argument("--format", choices=["generic", "chatgpt"], default="generic"); args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8")); texts = chatgpt_texts(data) if args.format == "chatgpt" else generic_texts(data)
    counts: Counter[str] = Counter()
    for text in texts:
        lower = text.lower()
        for category, patterns in PATTERNS.items():
            if any(pattern in lower for pattern in patterns): counts[category] += 1
    print(json.dumps({"items": len(texts), "categories": dict(counts)}, indent=2, ensure_ascii=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
