#!/usr/bin/env python3
"""Build an editing-ready raw SRT, word JSON, and matching audio handoff."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PHRASE_END = re.compile(r"[，。！？；：,.!?;:]\s*$")
NO_SPACE_BEFORE = frozenset("，。！？；：、,.!?;:%)]}”’")
NO_SPACE_AFTER = frozenset("([{“‘")


def fail(message: str) -> None:
    raise SystemExit(message)


def milliseconds(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def srt_timestamp(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def combine_text(left: str, right: str, language: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    if language.lower().startswith(("zh", "ja", "ko")):
        return left + right
    if right[0] in NO_SPACE_BEFORE or left[-1] in NO_SPACE_AFTER:
        return left + right
    return left + " " + right


def render_words(words: list[dict[str, Any]], language: str) -> str:
    raw = "".join(str(word.get("_raw_text", "")) for word in words).strip()
    if raw:
        return raw
    text = ""
    for word in words:
        text = combine_text(text, clean_text(word.get("text")), language)
    return text


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    source_segment_ids: list[int] = field(default_factory=list)


def normalized_words(segment: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for item in segment.get("words", []) or []:
        raw_text = str(item.get("word", item.get("text", "")))
        text = raw_text.strip()
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or end <= start or start < 0:
            continue
        words.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "_raw_text": raw_text,
            }
        )
    return sorted(words, key=lambda word: (word["start"], word["end"]))


def split_long_segment(
    segment: dict[str, Any],
    words: list[dict[str, Any]],
    language: str,
    minimum: float,
    maximum: float,
) -> list[Cue]:
    segment_id = int(segment.get("id", 0))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current and (
            word["end"] - current[0]["start"] > maximum
            or word["start"] - current[-1]["end"] > 1.5
        ):
            groups.append(current)
            current = []
        current.append(word)
        duration = current[-1]["end"] - current[0]["start"]
        if duration >= minimum and (PHRASE_END.search(word["text"]) or duration >= maximum):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    return [
        Cue(
            start=group[0]["start"],
            end=group[-1]["end"],
            text=render_words(group, language),
            words=group,
            source_segment_ids=[segment_id],
        )
        for group in groups
        if render_words(group, language)
    ]


def initial_cues(
    raw_segments: list[dict[str, Any]],
    language: str,
    minimum: float,
    maximum: float,
) -> tuple[list[Cue], list[str]]:
    cues: list[Cue] = []
    warnings: list[str] = []
    for position, segment in enumerate(raw_segments, start=1):
        text = clean_text(segment.get("text"))
        if not text:
            continue
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            fail(f"Raw segment {position} has invalid timestamps")
        if start < 0 or end <= start:
            fail(f"Raw segment {position} has invalid interval {start} -> {end}")
        segment_id = int(segment.get("id", position))
        words = normalized_words(segment)
        if end - start > maximum and words:
            cues.extend(split_long_segment(segment, words, language, minimum, maximum))
        else:
            if end - start > maximum:
                warnings.append(
                    f"segment {segment_id} is {end - start:.3f}s and has no usable word timestamps"
                )
            cues.append(
                Cue(
                    start=start,
                    end=end,
                    text=text,
                    words=words,
                    source_segment_ids=[segment_id],
                )
            )
    return sorted(cues, key=lambda cue: (cue.start, cue.end)), warnings


def merge_pair(left: Cue, right: Cue, language: str) -> Cue:
    return Cue(
        start=min(left.start, right.start),
        end=max(left.end, right.end),
        text=combine_text(left.text, right.text, language),
        words=left.words + right.words,
        source_segment_ids=list(dict.fromkeys(left.source_segment_ids + right.source_segment_ids)),
    )


def merge_short_cues(
    cues: list[Cue], language: str, minimum: float, maximum: float
) -> list[Cue]:
    merged: list[Cue] = []
    index = 0
    while index < len(cues):
        cue = cues[index]
        if cue.end - cue.start < minimum and index + 1 < len(cues):
            following = cues[index + 1]
            if following.end - cue.start <= maximum and following.start - cue.end <= 1.5:
                merged.append(merge_pair(cue, following, language))
                index += 2
                continue
        if cue.end - cue.start < minimum and merged:
            previous = merged[-1]
            if cue.end - previous.start <= maximum and cue.start - previous.end <= 1.5:
                merged[-1] = merge_pair(previous, cue, language)
                index += 1
                continue
        merged.append(cue)
        index += 1
    return merged


def finalize_cues(cues: list[Cue], duration: float) -> tuple[list[dict[str, Any]], list[str]]:
    duration_ms = milliseconds(duration)
    previous_end_ms = 0
    output: list[dict[str, Any]] = []
    warnings: list[str] = []
    for cue in cues:
        start_ms = max(previous_end_ms, milliseconds(cue.start))
        end_ms = min(duration_ms, milliseconds(cue.end))
        if start_ms > milliseconds(cue.start):
            warnings.append(
                f"clamped overlapping cue at {cue.start:.3f}s to {start_ms / 1000:.3f}s"
            )
        if end_ms <= start_ms:
            fail(
                "A cue became empty while resolving overlaps; inspect raw segments "
                f"{cue.source_segment_ids} around {cue.start:.3f}s"
            )
        words: list[dict[str, Any]] = []
        last_word_start = start_ms
        for word in cue.words:
            word_start_ms = max(start_ms, last_word_start, milliseconds(float(word["start"])))
            word_end_ms = min(end_ms, milliseconds(float(word["end"])))
            if word_end_ms <= word_start_ms:
                continue
            words.append(
                {
                    "text": clean_text(word["text"]),
                    "start": word_start_ms / 1000,
                    "end": word_end_ms / 1000,
                }
            )
            last_word_start = word_start_ms
        output.append(
            {
                "id": len(output) + 1,
                "start": start_ms / 1000,
                "end": end_ms / 1000,
                "text": cue.text,
                "words": words,
            }
        )
        previous_end_ms = end_ms
    return output, warnings


def choose_audio(manifest: dict[str, Any], mode: str) -> tuple[Path, float]:
    input_media = manifest["input"]
    if mode == "original" or (mode == "auto" and input_media.get("media_type") == "audio"):
        return Path(input_media["path"]), float(input_media["duration_seconds"])
    normalized = manifest["normalized_audio"]
    return Path(normalized["path"]), float(normalized["duration_seconds"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, help="Merged raw Whisper JSON")
    parser.add_argument("manifest", type=Path, help="prepare_media.py manifest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basename", help="Delivery basename; defaults to source media stem")
    parser.add_argument("--language", default="zh-CN", help="BCP-47 output language tag")
    parser.add_argument("--minimum-cue-seconds", type=float, default=1.0)
    parser.add_argument("--maximum-cue-seconds", type=float, default=8.0)
    parser.add_argument(
        "--audio-mode",
        choices=("auto", "original", "normalized"),
        default="auto",
        help="Auto keeps input audio, but extracts normalized WAV from video.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not 0 < args.minimum_cue_seconds <= args.maximum_cue_seconds:
        fail("Require 0 < minimum-cue-seconds <= maximum-cue-seconds")
    transcript_path = args.transcript.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audio_source, duration = choose_audio(manifest, args.audio_mode)
    audio_source = audio_source.expanduser().resolve()
    source_media = Path(manifest["input"]["path"])
    basename = args.basename or source_media.stem
    if not basename or Path(basename).name != basename:
        fail("basename must be one filename stem without path separators")

    cues, warnings = initial_cues(
        transcript.get("segments", []),
        args.language,
        args.minimum_cue_seconds,
        args.maximum_cue_seconds,
    )
    if not cues:
        fail("Transcript has no usable speech segments")
    cues = merge_short_cues(
        cues, args.language, args.minimum_cue_seconds, args.maximum_cue_seconds
    )
    segments, timeline_warnings = finalize_cues(cues, duration)
    warnings.extend(timeline_warnings)

    if not audio_source.is_file():
        fail(f"Audio handoff source does not exist: {audio_source}")
    output_dir = args.output_dir.expanduser().resolve()
    audio_path = output_dir / f"{basename}{audio_source.suffix.lower()}"
    srt_path = output_dir / f"{basename}.{args.language}.raw.srt"
    json_path = output_dir / f"{basename}.{args.language}.words.json"
    existing = [path for path in (audio_path, srt_path, json_path) if path.exists()]
    if existing and not args.overwrite:
        fail("Output exists; choose a new directory or pass --overwrite: " + ", ".join(map(str, existing)))
    output_dir.mkdir(parents=True, exist_ok=True)
    if audio_source != audio_path:
        shutil.copy2(audio_source, audio_path)

    srt_blocks = []
    for segment in segments:
        srt_blocks.append(
            "\n".join(
                (
                    str(segment["id"]),
                    f"{srt_timestamp(milliseconds(segment['start']))} --> "
                    f"{srt_timestamp(milliseconds(segment['end']))}",
                    segment["text"],
                )
            )
        )
    srt_path.write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")

    word_count = sum(len(segment["words"]) for segment in segments)
    payload = {
        "schema_version": "1.0",
        "language": args.language,
        "audio_file": audio_path.name,
        "duration_seconds": round(duration, 3),
        "timestamp_source": (
            "mlx-whisper-cross-attention-dtw" if word_count else "segment-level-only"
        ),
        "segments": segments,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "audio": str(audio_path),
        "srt": str(srt_path),
        "words_json": str(json_path),
        "duration_seconds": round(duration, 3),
        "cue_count": len(segments),
        "word_timestamp_count": word_count,
        "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
