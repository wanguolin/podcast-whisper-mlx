#!/usr/bin/env python3
"""Render raw transcript JSON as timestamp-free content Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def render_content_markdown(data: dict[str, Any]) -> str:
    paragraphs = [
        str(segment.get("text", "")).strip()
        for segment in data.get("segments", [])
        if str(segment.get("text", "")).strip()
    ]
    if not paragraphs:
        raise ValueError("transcript has no non-empty text segments")
    return "\n\n".join(paragraphs) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_path = args.input_json.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else input_path.with_suffix(".md")
    )
    if output_path.exists() and not args.overwrite:
        raise SystemExit(f"Output exists; pass --overwrite: {output_path}")
    data = json.loads(input_path.read_text(encoding="utf-8"))
    try:
        markdown = render_content_markdown(data)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    print(json.dumps({"output": str(output_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
