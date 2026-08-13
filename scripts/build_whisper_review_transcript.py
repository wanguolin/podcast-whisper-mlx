#!/usr/bin/env python3
"""Build review-friendly JSON, Markdown, and SRT from an MLX Whisper result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def clock(seconds: float, *, srt: bool = False) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_prefix", type=Path)
    parser.add_argument("--cutoff", type=float, default=4809.5)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    source_segments = source.get("segments", [])
    kept = []
    rejected = {"empty_text": 0, "invalid_or_zero_duration": 0, "after_cutoff": 0}

    for item in source_segments:
        start = float(item.get("start", 0.0))
        end = float(item.get("end", 0.0))
        text = str(item.get("text", "")).strip()
        if not text:
            rejected["empty_text"] += 1
            continue
        if end <= start:
            rejected["invalid_or_zero_duration"] += 1
            continue
        if end > args.cutoff:
            rejected["after_cutoff"] += 1
            continue
        kept.append(
            {
                "id": len(kept) + 1,
                "start": start,
                "end": end,
                "text": text,
                "avg_logprob": item.get("avg_logprob"),
                "no_speech_prob": item.get("no_speech_prob"),
            }
        )

    payload = {
        "status": "machine transcript; human review required",
        "speaker_labels": False,
        "source": str(args.input),
        "cutoff_seconds": args.cutoff,
        "postprocessing": (
            "Removed empty/zero-or-negative-duration segments and segments beyond the "
            "manually verified final spoken-content cutoff; no wording was corrected."
        ),
        "source_segment_count": len(source_segments),
        "segment_count": len(kept),
        "rejected": rejected,
        "segments": kept,
    }

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.output_prefix.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    md_lines = [
        "# All-In E264 — Whisper 机器审阅稿",
        "",
        "> 未经人工逐句校对；没有说话人标签。已删除空片段、无效时间轴和结尾音乐上的重复幻觉。",
        "",
    ]
    md_lines.extend(item["text"] for item in kept)
    args.output_prefix.with_suffix(".md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    srt_blocks = []
    for item in kept:
        srt_blocks.append(
            f"{item['id']}\n{clock(item['start'], srt=True)} --> {clock(item['end'], srt=True)}\n{item['text']}"
        )
    args.output_prefix.with_suffix(".srt").write_text(
        "\n\n".join(srt_blocks) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
