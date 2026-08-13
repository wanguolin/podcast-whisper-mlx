#!/usr/bin/env python3
"""Build a human-review transcript from the MOSS diarized JSON output."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SPEAKER_NAMES = {
    "S01": "西卡",
    "S02": "万国",
    # The recording has two hosts. MOSS fragmented five short 万国 turns as S03.
    "S03": "万国",
}


def timestamp(seconds: float, decimal_mark: str = ".") -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal_mark}{millis:03d}"


def clean_text(text: str) -> str:
    return re.sub(r"^\[S\d+\]\s*", "", text).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_prefix", type=Path)
    parser.add_argument(
        "--speech-cutoff",
        type=float,
        default=2158.0,
        help="Discard model output after the spoken program ends.",
    )
    parser.add_argument(
        "--first-segment-start",
        type=float,
        default=7.54,
        help="Correct the hotword run's 0s intro boundary using the baseline run.",
    )
    args = parser.parse_args()

    raw = json.loads(args.input_json.read_text(encoding="utf-8"))
    segments = []
    for index, segment in enumerate(raw["segments"]):
        if segment["start"] >= args.speech_cutoff:
            continue
        original_speaker = segment.get("speaker_id", "UNKNOWN")
        speaker_name = SPEAKER_NAMES.get(original_speaker, original_speaker)
        start = args.first_segment_start if index == 0 else segment["start"]
        segments.append(
            {
                "start": start,
                "end": segment["end"],
                "speaker": speaker_name,
                "original_speaker_id": original_speaker,
                "text": clean_text(segment["text"]),
            }
        )

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)

    review_json = {
        "source": str(args.input_json),
        "status": "machine transcript requiring human review",
        "postprocessing": [
            "Mapped S01 to 西卡.",
            "Mapped S02 and fragmented S03 turns to 万国.",
            f"Corrected the first cue start to {args.first_segment_start:.3f}s using the baseline MOSS run.",
            f"Discarded output beginning at {args.speech_cutoff:.3f}s or later as outro hallucination.",
        ],
        "segments": segments,
    }
    args.output_prefix.with_suffix(".json").write_text(
        json.dumps(review_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with args.output_prefix.with_suffix(".srt").open("w", encoding="utf-8") as handle:
        for index, segment in enumerate(segments, 1):
            handle.write(f"{index}\n")
            handle.write(
                f"{timestamp(segment['start'], ',')} --> "
                f"{timestamp(segment['end'], ',')}\n"
            )
            handle.write(f"{segment['speaker']}：{segment['text']}\n\n")

    with args.output_prefix.with_suffix(".md").open("w", encoding="utf-8") as handle:
        handle.write("# Sila Sveta 播客机器转写审校稿\n\n")
        handle.write(
            "> MOSS-Transcribe-Diarize 机器转写；已合并误拆的 S03，"
            "专有名词和个别时间轴仍需人工审校。\n\n"
        )
        for segment in segments:
            handle.write(
                f"**{segment['speaker']}：** {segment['text']}\n\n"
            )


if __name__ == "__main__":
    main()
