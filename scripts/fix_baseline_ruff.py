#!/usr/bin/env python3
"""Apply the non-autofixable baseline Ruff repairs identified by CI.

This script is intentionally narrow and fails if any expected source pattern is
missing, so it cannot silently rewrite an unexpected version of the file.
"""

from pathlib import Path


# Kept as a temporary branch-only repair tool; remove after the generated fix lands.
TARGET = Path("core/framework/server/routes_execution.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "            import asyncio\n\n            for _ in range(50):  # Max 5 seconds",
        "            for _ in range(50):  # Max 5 seconds",
        "remove local asyncio import",
    )
    text = replace_once(
        text,
        "                def _inspect_pdf() -> tuple[int, list[str]]:",
        "                def _inspect_pdf(\n"
        "                    _pdf_bytes=_pdf_bytes,\n"
        "                    pdf_filepath=pdf_filepath,\n"
        "                    is_large=is_large,\n"
        "                ) -> tuple[int, list[str]]:",
        "bind PDF helper loop values",
    )
    text = replace_once(
        text,
        "                def _parse_csv() -> tuple[int, str | None]:",
        "                def _parse_csv(\n"
        "                    _csv_bytes=_csv_bytes,\n"
        "                    csv_filepath=csv_filepath,\n"
        "                    csv_filename=csv_filename,\n"
        "                ) -> tuple[int, str | None]:",
        "bind CSV helper loop values",
    )
    text = replace_once(
        text,
        "                def _read_text_attachment() -> tuple[str, int]:",
        "                def _read_text_attachment(\n"
        "                    _text_bytes=_text_bytes,\n"
        "                    is_large=is_large,\n"
        "                    text_filepath=text_filepath,\n"
        "                ) -> tuple[str, int]:",
        "bind text helper loop values",
    )
    text = replace_once(
        text,
        "                    def _probe_dims() -> str:",
        "                    def _probe_dims(_img_bytes=_img_bytes) -> str:",
        "bind image helper loop value",
    )

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
