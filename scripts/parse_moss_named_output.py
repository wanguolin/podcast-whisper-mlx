#!/usr/bin/env python3
"""Parse MOSS output that unexpectedly uses [time]Name: text[time] records."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


PATTERN = re.compile(
    r"\[(?P<start>\d+(?:\.\d+)?)\]"
    r"(?P<speaker>[^\[\]:]{1,80}):\s*"
    r"(?P<text>.*?)"
    r"\[(?P<end>\d+(?:\.\d+)?)\]",
    re.DOTALL,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--offset", type=float, default=0.0)
    parser.add_argument("--chunk", required=True)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    raw = str(source.get("text", ""))
    segments = []
    for index, match in enumerate(PATTERN.finditer(raw), start=1):
        relative_start = float(match.group("start"))
        relative_end = float(match.group("end"))
        segments.append(
            {
                "id": index,
                "chunk": args.chunk,
                "relative_start": relative_start,
                "relative_end": relative_end,
                "absolute_start": relative_start + args.offset,
                "absolute_end": relative_end + args.offset,
                "model_speaker_guess": match.group("speaker").strip(),
                "text": " ".join(match.group("text").split()),
            }
        )

    payload = {
        "status": "parsed machine output; human review required",
        "warning": (
            "model_speaker_guess values are untrusted generated names, not verified speaker IDs; "
            "they drift within and across chunks"
        ),
        "source": str(args.input),
        "chunk": args.chunk,
        "offset_seconds": args.offset,
        "segment_count": len(segments),
        "speaker_guess_counts": dict(Counter(item["model_speaker_guess"] for item in segments)),
        "segments": segments,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
