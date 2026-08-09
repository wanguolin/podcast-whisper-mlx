#!/usr/bin/env python3
"""Extract a normalized audio track and split it near long silences.

The chunk manifest separates each chunk's core interval from its decoded
interval. Decoded intervals include overlap so ASR sees boundary context; later
stages keep only segments whose midpoint belongs to the core interval.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END = re.compile(
    r"silence_end:\s*(-?\d+(?:\.\d+)?)\s*\|\s*silence_duration:\s*(\d+(?:\.\d+)?)"
)


def fail(message: str) -> None:
    raise SystemExit(message)


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=capture,
        check=False,
    )
    if completed.returncode:
        details = (completed.stderr or completed.stdout or "").strip()
        fail(f"Command failed ({completed.returncode}): {' '.join(command)}\n{details}")
    return completed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path) -> dict[str, Any]:
    completed = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size:stream=index,codec_type,codec_name,sample_rate,channels,width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(completed.stdout)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        fail(f"No audio stream found: {path}")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        fail(f"Unable to determine media duration: {path}")
    if duration <= 0:
        fail(f"Invalid media duration {duration}: {path}")
    data["duration_seconds"] = duration
    data["has_video"] = any(s.get("codec_type") == "video" for s in data.get("streams", []))
    return data


def detect_silences(
    wav_path: Path,
    duration: float,
    noise_db: float,
    minimum_silence: float,
) -> list[dict[str, float]]:
    expression = f"silencedetect=noise={noise_db:g}dB:d={minimum_silence:g}"
    completed = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav_path),
            "-af",
            expression,
            "-f",
            "null",
            "-",
        ]
    )
    silences: list[dict[str, float]] = []
    pending_start: float | None = None
    for line in completed.stderr.splitlines():
        start_match = SILENCE_START.search(line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
        end_match = SILENCE_END.search(line)
        if end_match:
            end = min(duration, float(end_match.group(1)))
            measured_duration = float(end_match.group(2))
            start = pending_start if pending_start is not None else max(0.0, end - measured_duration)
            if end > start:
                silences.append(
                    {
                        "start": start,
                        "end": end,
                        "duration": end - start,
                        "midpoint": (start + end) / 2,
                    }
                )
            pending_start = None
    if pending_start is not None and duration > pending_start:
        silences.append(
            {
                "start": pending_start,
                "end": duration,
                "duration": duration - pending_start,
                "midpoint": (pending_start + duration) / 2,
            }
        )
    return silences


def choose_boundaries(
    duration: float,
    silences: list[dict[str, float]],
    minimum_core: float,
    target_core: float,
    maximum_core: float,
) -> tuple[list[float], list[str]]:
    if duration <= maximum_core:
        return [0.0, duration], []

    warnings: list[str] = []
    boundaries = [0.0]
    cursor = 0.0
    while duration - cursor > maximum_core:
        lower = cursor + minimum_core
        upper = min(cursor + maximum_core, duration - minimum_core)
        if upper <= lower:
            upper = min(cursor + maximum_core, duration)
        target = min(cursor + target_core, upper)
        candidates = [s for s in silences if lower <= s["midpoint"] <= upper]
        if candidates:
            chosen = min(
                candidates,
                key=lambda item: (abs(item["midpoint"] - target), -item["duration"]),
            )
            cut = chosen["midpoint"]
        else:
            cut = target
            warnings.append(
                f"No silence boundary found between {lower:.3f}s and {upper:.3f}s; "
                f"used fallback cut at {cut:.3f}s. Review this join."
            )
        if cut <= cursor:
            fail("Internal error: non-increasing chunk boundary")
        boundaries.append(cut)
        cursor = cut
    boundaries.append(duration)
    return boundaries, warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract 16 kHz mono audio and split long media at silence boundaries."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-core-seconds", type=float, default=900.0)
    parser.add_argument("--target-core-seconds", type=float, default=1500.0)
    parser.add_argument("--maximum-core-seconds", type=float, default=1800.0)
    parser.add_argument("--overlap-seconds", type=float, default=8.0)
    parser.add_argument("--silence-min-seconds", type=float, default=0.8)
    parser.add_argument("--silence-noise-db", type=float, default=-35.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        fail("ffmpeg and ffprobe are required on PATH")
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_path.is_file():
        fail(f"Input file not found: {input_path}")
    if not (0 < args.minimum_core_seconds <= args.target_core_seconds <= args.maximum_core_seconds):
        fail("Require 0 < minimum-core <= target-core <= maximum-core")
    if not 0 <= args.overlap_seconds < args.minimum_core_seconds / 2:
        fail("overlap-seconds must be non-negative and less than half minimum-core-seconds")
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists() and not args.overwrite:
        fail(f"Output already exists; choose a new directory or pass --overwrite: {manifest_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    media_probe = probe(input_path)
    duration = media_probe["duration_seconds"]

    normalized_wav = output_dir / "source.16k-mono.wav"
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if args.overwrite else "-n",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(normalized_wav),
        ]
    )

    wav_probe = probe(normalized_wav)
    wav_duration = wav_probe["duration_seconds"]
    silences = detect_silences(
        normalized_wav,
        wav_duration,
        args.silence_noise_db,
        args.silence_min_seconds,
    )
    boundaries, warnings = choose_boundaries(
        wav_duration,
        silences,
        args.minimum_core_seconds,
        args.target_core_seconds,
        args.maximum_core_seconds,
    )

    chunks: list[dict[str, Any]] = []
    for index, (core_start, core_end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        decode_start = max(0.0, core_start - args.overlap_seconds)
        decode_end = min(wav_duration, core_end + args.overlap_seconds)
        chunk_path = chunks_dir / f"chunk-{index:04d}.wav"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y" if args.overwrite else "-n",
                "-ss",
                f"{decode_start:.6f}",
                "-t",
                f"{decode_end - decode_start:.6f}",
                "-i",
                str(normalized_wav),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(chunk_path),
            ]
        )
        chunks.append(
            {
                "id": index,
                "path": str(chunk_path),
                "core_start": core_start,
                "core_end": core_end,
                "decode_start": decode_start,
                "decode_end": decode_end,
                "core_duration": core_end - core_start,
                "decode_duration": decode_end - decode_start,
                "sha256": sha256(chunk_path),
            }
        )

    manifest = {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": sha256(input_path),
            "media_type": "video" if media_probe["has_video"] else "audio",
            "duration_seconds": duration,
            "probe": media_probe,
        },
        "normalized_audio": {
            "path": str(normalized_wav),
            "sha256": sha256(normalized_wav),
            "duration_seconds": wav_duration,
            "sample_rate": 16000,
            "channels": 1,
            "codec": "pcm_s16le",
        },
        "split_strategy": {
            "method": "ffmpeg silencedetect midpoint with bounded fallback",
            "minimum_core_seconds": args.minimum_core_seconds,
            "target_core_seconds": args.target_core_seconds,
            "maximum_core_seconds": args.maximum_core_seconds,
            "overlap_seconds": args.overlap_seconds,
            "silence_min_seconds": args.silence_min_seconds,
            "silence_noise_db": args.silence_noise_db,
            "silence_count": len(silences),
            "warnings": warnings,
        },
        "chunks": chunks,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "media_type": manifest["input"]["media_type"],
                "duration_seconds": duration,
                "chunk_count": len(chunks),
                "silence_count": len(silences),
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
