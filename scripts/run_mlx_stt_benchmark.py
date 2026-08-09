#!/usr/bin/env python3
"""Run a concise MLX-Audio STT benchmark without dumping the transcript to stdout."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import mlx.core as mx

from mlx_audio.stt import load


def compact_segment(segment: dict) -> dict:
    keep = (
        "id",
        "start",
        "end",
        "text",
        "speaker_id",
        "temperature",
        "avg_logprob",
        "compression_ratio",
        "no_speech_prob",
    )
    return {key: segment[key] for key in keep if key in segment}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("moss", "whisper"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audio-duration", type=float, required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-tokens", type=int, default=65_536)
    args = parser.parse_args()

    wall_start = time.perf_counter()
    load_start = wall_start
    model = load(args.model)
    mx.synchronize()
    load_seconds = time.perf_counter() - load_start

    mx.reset_peak_memory()
    inference_start = time.perf_counter()
    if args.mode == "moss":
        result = model.generate(
            args.audio,
            max_tokens=args.max_tokens,
            temperature=0.0,
            prompt=args.prompt,
            prefill_step_size=4096,
        )
    else:
        result = model.generate(
            args.audio,
            language="en",
            initial_prompt=args.prompt or None,
            chunk_duration=30.0,
            word_timestamps=False,
            verbose=False,
        )
    mx.synchronize()
    inference_seconds = time.perf_counter() - inference_start

    segments = [compact_segment(item) for item in (result.segments or [])]
    metrics = {
        "mode": args.mode,
        "model": args.model,
        "audio": args.audio,
        "audio_duration_seconds": args.audio_duration,
        "load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "wall_seconds": time.perf_counter() - wall_start,
        "realtime_factor": inference_seconds / args.audio_duration,
        "realtime_speed": args.audio_duration / inference_seconds,
        "peak_mlx_memory_gb": mx.get_peak_memory() / 1e9,
        "prompt_tokens": getattr(result, "prompt_tokens", 0),
        "generation_tokens": getattr(result, "generation_tokens", 0),
        "segment_count": len(segments),
    }
    payload = {"metrics": metrics, "text": result.text, "segments": segments}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
