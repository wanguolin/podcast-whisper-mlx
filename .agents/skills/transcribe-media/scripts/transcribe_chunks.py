#!/usr/bin/env python3
"""Transcribe a prepared chunk manifest with MLX Whisper and merge overlaps."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))

import mlx.core as mx
from mlx_audio.stt import load

from render_raw_markdown import render_content_markdown


DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo-asr-fp16"


def compact_segment(item: Any) -> dict[str, Any]:
    keys = (
        "id",
        "start",
        "end",
        "text",
        "words",
        "temperature",
        "avg_logprob",
        "compression_ratio",
        "no_speech_prob",
    )
    return {key: getattr(item, key, item.get(key) if isinstance(item, dict) else None) for key in keys if (isinstance(item, dict) and key in item) or hasattr(item, key)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--language", default="auto", help="ISO code such as en/zh, or auto")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument(
        "--word-timestamps",
        action="store_true",
        help="Request cross-attention/DTW word timestamps from Whisper.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    combined_path = output_dir / "transcript.raw.json"
    if combined_path.exists() and not args.overwrite:
        raise SystemExit(f"Output exists; choose another directory or pass --overwrite: {combined_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunks = manifest.get("chunks", [])
    if not chunks:
        raise SystemExit("Manifest has no chunks")
    duration = float(manifest["normalized_audio"]["duration_seconds"])
    prompt = args.prompt
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()

    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_results_dir = output_dir / "chunks"
    chunk_results_dir.mkdir(parents=True, exist_ok=True)

    load_started = time.perf_counter()
    model = load(args.model)
    mx.synchronize()
    load_seconds = time.perf_counter() - load_started
    mx.reset_peak_memory()

    detected_language: str | None = None
    merged_segments: list[dict[str, Any]] = []
    chunk_metrics: list[dict[str, Any]] = []
    rejected = {"empty": 0, "invalid_time": 0, "overlap_duplicate": 0, "past_media_end": 0}
    inference_total = 0.0

    for chunk in chunks:
        chunk_id = int(chunk["id"])
        chunk_path = Path(chunk["path"])
        forced_language = None if args.language == "auto" else args.language
        if args.language == "auto" and detected_language:
            forced_language = detected_language
        inference_started = time.perf_counter()
        result = model.generate(
            str(chunk_path),
            language=forced_language,
            task="transcribe",
            initial_prompt=prompt or None,
            chunk_duration=30.0,
            word_timestamps=args.word_timestamps,
            condition_on_previous_text=True,
            verbose=False,
        )
        mx.synchronize()
        inference_seconds = time.perf_counter() - inference_started
        inference_total += inference_seconds
        result_language = getattr(result, "language", None)
        if result_language and detected_language is None:
            detected_language = str(result_language)

        raw_segments = [compact_segment(item) for item in (getattr(result, "segments", None) or [])]
        chunk_payload = {
            "chunk": chunk,
            "language": result_language,
            "text": getattr(result, "text", ""),
            "segments": raw_segments,
        }
        chunk_result_path = chunk_results_dir / f"chunk-{chunk_id:04d}.json"
        chunk_result_path.write_text(
            json.dumps(chunk_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        kept_count = 0
        for item in raw_segments:
            text = str(item.get("text", "")).strip()
            local_start = float(item.get("start", 0.0))
            local_end = float(item.get("end", 0.0))
            global_start = float(chunk["decode_start"]) + local_start
            global_end = float(chunk["decode_start"]) + local_end
            midpoint = (global_start + global_end) / 2
            if not text:
                rejected["empty"] += 1
                continue
            if global_end <= global_start:
                rejected["invalid_time"] += 1
                continue
            if midpoint < float(chunk["core_start"]) or midpoint >= float(chunk["core_end"]):
                rejected["overlap_duplicate"] += 1
                continue
            if global_start >= duration or global_end > duration + 1.0:
                rejected["past_media_end"] += 1
                continue
            segment = {key: value for key, value in item.items() if key not in {"id", "start", "end"}}
            if args.word_timestamps:
                global_words = []
                for word in item.get("words", []) or []:
                    word_start = float(chunk["decode_start"]) + float(word.get("start", 0.0))
                    word_end = float(chunk["decode_start"]) + float(word.get("end", 0.0))
                    if word_end <= word_start or word_start < 0 or word_start >= duration:
                        rejected["invalid_word_time"] = rejected.get("invalid_word_time", 0) + 1
                        continue
                    global_words.append(
                        {
                            **word,
                            "start": word_start,
                            "end": min(word_end, duration),
                        }
                    )
                segment["words"] = global_words
            segment.update(
                {
                    "id": len(merged_segments) + 1,
                    "chunk_id": chunk_id,
                    "start": global_start,
                    "end": min(global_end, duration),
                    "text": text,
                }
            )
            merged_segments.append(segment)
            kept_count += 1
        chunk_metrics.append(
            {
                "chunk_id": chunk_id,
                "audio_seconds": float(chunk["decode_duration"]),
                "inference_seconds": inference_seconds,
                "realtime_speed": float(chunk["decode_duration"]) / inference_seconds,
                "raw_segment_count": len(raw_segments),
                "kept_segment_count": kept_count,
            }
        )

    merged_segments.sort(key=lambda item: (item["start"], item["end"]))
    for index, item in enumerate(merged_segments, start=1):
        item["id"] = index

    payload = {
        "status": "machine transcript; calibration required",
        "speaker_labels": False,
        "source_manifest": str(manifest_path),
        "source_file": manifest["input"]["path"],
        "source_language": detected_language or (None if args.language == "auto" else args.language),
        "model": args.model,
        "prompt": prompt,
        "metrics": {
            "media_duration_seconds": duration,
            "model_load_seconds": load_seconds,
            "inference_seconds": inference_total,
            "realtime_speed": duration / inference_total,
            "peak_mlx_memory_gb": mx.get_peak_memory() / 1e9,
            "chunk_count": len(chunks),
            "segment_count": len(merged_segments),
            "word_timestamp_count": sum(
                len(item.get("words", []) or []) for item in merged_segments
            ),
            "rejected": rejected,
            "chunks": chunk_metrics,
        },
        "segments": merged_segments,
    }
    combined_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (output_dir / "transcript.raw.md").write_text(
        render_content_markdown(payload),
        encoding="utf-8",
    )
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
