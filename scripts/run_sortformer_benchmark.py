#!/usr/bin/env python3
"""Benchmark MLX Sortformer speaker diarization and save compact JSON."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import mlx.core as mx

from mlx_audio.vad import load


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audio-duration", type=float, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--min-duration", type=float, default=0.25)
    parser.add_argument("--merge-gap", type=float, default=0.2)
    args = parser.parse_args()

    wall_start = time.perf_counter()
    model = load(args.model)
    mx.synchronize()
    load_seconds = time.perf_counter() - wall_start

    mx.reset_peak_memory()
    inference_start = time.perf_counter()
    result = model.generate(
        args.audio,
        threshold=args.threshold,
        min_duration=args.min_duration,
        merge_gap=args.merge_gap,
        verbose=False,
    )
    mx.synchronize()
    inference_seconds = time.perf_counter() - inference_start

    segments = [
        {
            "start": float(segment.start),
            "end": float(segment.end),
            "speaker": int(segment.speaker),
        }
        for segment in result.segments
    ]
    metrics = {
        "model": args.model,
        "audio_duration_seconds": args.audio_duration,
        "load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "wall_seconds": time.perf_counter() - wall_start,
        "realtime_factor": inference_seconds / args.audio_duration,
        "realtime_speed": args.audio_duration / inference_seconds,
        "peak_mlx_memory_gb": mx.get_peak_memory() / 1e9,
        "num_speakers": int(result.num_speakers),
        "segment_count": len(segments),
        "threshold": args.threshold,
        "min_duration": args.min_duration,
        "merge_gap": args.merge_gap,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"metrics": metrics, "segments": segments}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
