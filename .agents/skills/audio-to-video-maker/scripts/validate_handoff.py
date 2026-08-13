#!/usr/bin/env python3
"""Validate an audio + raw SRT + word JSON editing handoff."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


TIMECODE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def fail(message: str) -> None:
    raise SystemExit(message)


def parse_time(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = map(int, parts)
    if minutes >= 60 or seconds >= 60:
        fail("Invalid SRT minute or second field")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip()) if text.strip() else []
    cues: list[dict[str, Any]] = []
    for expected, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            fail(f"SRT cue {expected} is incomplete")
        if lines[0] != str(expected):
            fail(f"SRT cue index {lines[0]!r} should be {expected}")
        match = TIMECODE.fullmatch(lines[1])
        if not match:
            fail(f"SRT cue {expected} has a non-standard timecode: {lines[1]}")
        start_ms = parse_time(match.groups()[:4])
        end_ms = parse_time(match.groups()[4:])
        cue_text = "\n".join(lines[2:])
        if not cue_text.strip():
            fail(f"SRT cue {expected} has empty text")
        cues.append({"id": expected, "start_ms": start_ms, "end_ms": end_ms, "text": cue_text})
    return cues


def audio_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        fail(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    try:
        return float(completed.stdout.strip())
    except ValueError:
        fail(f"ffprobe returned an invalid duration for {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("srt", type=Path)
    parser.add_argument("words_json", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--minimum-cue-seconds", type=float, default=1.0)
    parser.add_argument("--maximum-cue-seconds", type=float, default=8.0)
    parser.add_argument("--duration-tolerance", type=float, default=0.1)
    args = parser.parse_args()

    srt_path = args.srt.expanduser().resolve()
    json_path = args.words_json.expanduser().resolve()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        fail("words JSON schema_version must be 1.0")
    language = str(payload.get("language", ""))
    if not language:
        fail("words JSON language is required")
    audio_path = (
        args.audio.expanduser().resolve()
        if args.audio
        else (json_path.parent / str(payload.get("audio_file", ""))).resolve()
    )
    if not audio_path.is_file():
        fail(f"Audio file is missing: {audio_path}")
    if payload.get("audio_file") != audio_path.name:
        fail("words JSON audio_file does not match the delivered audio filename")
    expected_srt = f"{audio_path.stem}.{language}.raw.srt"
    expected_json = f"{audio_path.stem}.{language}.words.json"
    if srt_path.name != expected_srt or json_path.name != expected_json:
        fail(
            "Audio, SRT, and JSON basenames do not match the naming contract: "
            f"expected {expected_srt} and {expected_json}"
        )

    duration = audio_duration(audio_path)
    declared_duration = float(payload.get("duration_seconds", -1))
    if abs(declared_duration - duration) > args.duration_tolerance:
        fail(
            f"JSON duration {declared_duration:.3f}s differs from audio duration "
            f"{duration:.3f}s"
        )
    duration_ms = round(duration * 1000)
    cues = parse_srt(srt_path)
    segments = payload.get("segments", [])
    if not cues or len(cues) != len(segments):
        fail("SRT cues and words JSON segments must be non-empty and one-to-one")

    previous_end_ms = 0
    word_count = 0
    warnings: list[str] = []
    for cue, segment in zip(cues, segments):
        cue_id = cue["id"]
        if segment.get("id") != cue_id:
            fail(f"JSON segment id does not match SRT cue {cue_id}")
        if cue["start_ms"] < previous_end_ms:
            fail(f"SRT cue {cue_id} overlaps or moves backward")
        if cue["end_ms"] <= cue["start_ms"]:
            fail(f"SRT cue {cue_id} has a non-positive duration")
        if cue["end_ms"] > duration_ms + round(args.duration_tolerance * 1000):
            fail(f"SRT cue {cue_id} exceeds the audio duration")
        if cue["text"] != str(segment.get("text", "")):
            fail(f"SRT cue {cue_id} text differs from words JSON")
        if round(float(segment["start"]) * 1000) != cue["start_ms"] or round(
            float(segment["end"]) * 1000
        ) != cue["end_ms"]:
            fail(f"SRT cue {cue_id} timestamps differ from words JSON")

        cue_duration = (cue["end_ms"] - cue["start_ms"]) / 1000
        if cue_duration < args.minimum_cue_seconds:
            warnings.append(f"cue {cue_id} is short ({cue_duration:.3f}s)")
        if cue_duration > args.maximum_cue_seconds:
            warnings.append(f"cue {cue_id} is long ({cue_duration:.3f}s)")

        previous_word_start = cue["start_ms"]
        for word in segment.get("words", []) or []:
            text = str(word.get("text", "")).strip()
            word_start_ms = round(float(word["start"]) * 1000)
            word_end_ms = round(float(word["end"]) * 1000)
            if not text:
                fail(f"JSON segment {cue_id} has an empty word token")
            if word_start_ms < cue["start_ms"] or word_end_ms > cue["end_ms"]:
                fail(f"JSON segment {cue_id} has a word outside its cue interval")
            if word_start_ms < previous_word_start or word_end_ms <= word_start_ms:
                fail(f"JSON segment {cue_id} has invalid or backward word timestamps")
            previous_word_start = word_start_ms
            word_count += 1
        previous_end_ms = cue["end_ms"]

    summary = {
        "status": "valid",
        "audio": str(audio_path),
        "duration_seconds": round(duration, 3),
        "cue_count": len(cues),
        "word_timestamp_count": word_count,
        "first_cue_start": cues[0]["start_ms"] / 1000,
        "last_cue_end": cues[-1]["end_ms"] / 1000,
        "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
