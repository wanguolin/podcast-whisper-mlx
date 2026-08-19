#!/usr/bin/env python3
"""Run chunked MOSS diarization and conservatively reconcile overlap labels.

Generated names are never treated as identities. Every raw label/name is first
converted to a chunk-local anonymous label. Only matching speech in adjacent
overlaps may join two local labels into one global anonymous speaker.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))


DEFAULT_MODEL = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
RAW_RECORD = re.compile(
    r"\[(?P<start>\d+(?:\.\d+)?)\]"
    r"(?:\[(?P<bracket>[^\]]{1,80})\]|(?P<named>[^\[\]:]{1,80}):)\s*"
    r"(?P<text>.*?)"
    r"\[(?P<end>\d+(?:\.\d+)?)\]",
    re.DOTALL,
)
LABEL_PREFIX = re.compile(r"^\[(?P<label>[^\]]+)\]\s*")


def configure_model_network(allow_download: bool) -> None:
    """Keep model loading offline unless the caller explicitly opts in."""
    if allow_download:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[\w']+", text.lower(), flags=re.UNICODE))


def parse_records(result: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in (getattr(result, "segments", None) or []):
        start = float(value(item, "start", 0.0))
        end = float(value(item, "end", 0.0))
        text = str(value(item, "text", "")).strip()
        speaker = value(item, "speaker_id")
        prefix = LABEL_PREFIX.match(text)
        if speaker is None and prefix:
            speaker = prefix.group("label")
            text = text[prefix.end() :].strip()
        if speaker is not None and end > start and text:
            records.append({"start": start, "end": end, "raw_label": str(speaker), "text": text})
    if records:
        return records

    raw_text = str(getattr(result, "text", ""))
    for match in RAW_RECORD.finditer(raw_text):
        start = float(match.group("start"))
        end = float(match.group("end"))
        text = " ".join(match.group("text").split())
        raw_label = (match.group("bracket") or match.group("named") or "UNKNOWN").strip()
        if end > start and text:
            records.append({"start": start, "end": end, "raw_label": raw_label, "text": text})
    return records


def local_anonymize(records: list[dict[str, Any]], chunk_id: int) -> list[dict[str, Any]]:
    mapping: dict[str, str] = {}
    output = []
    for record in records:
        raw_label = record["raw_label"]
        if raw_label not in mapping:
            mapping[raw_label] = f"L{len(mapping) + 1:02d}"
        output.append(
            {
                **record,
                "chunk_id": chunk_id,
                "local_label": mapping[raw_label],
                "local_key": f"C{chunk_id:04d}_{mapping[raw_label]}",
            }
        )
    return output


def intervals_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(0.0, min(left["global_end"], right["global_end"]) - max(left["global_start"], right["global_start"]))


def reconcile_overlaps(
    all_records: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    union: UnionFind,
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in all_records:
        by_chunk[record["chunk_id"]].append(record)
        union.add(record["local_key"])

    for previous, current in zip(chunks, chunks[1:]):
        previous_id, current_id = int(previous["id"]), int(current["id"])
        overlap_start = max(float(previous["decode_start"]), float(current["decode_start"]))
        overlap_end = min(float(previous["decode_end"]), float(current["decode_end"]))
        if overlap_end <= overlap_start:
            continue
        previous_active = {
            record["local_key"]
            for record in by_chunk[previous_id]
            if record["global_end"] > overlap_start and record["global_start"] < overlap_end
        }
        current_active = {
            record["local_key"]
            for record in by_chunk[current_id]
            if record["global_end"] > overlap_start and record["global_start"] < overlap_end
        }
        if len(previous_active) == 1 and len(current_active) == 1:
            left_key = next(iter(previous_active))
            right_key = next(iter(current_active))
            union.union(left_key, right_key)
            links.append(
                {
                    "previous_chunk": previous_id,
                    "current_chunk": current_id,
                    "previous_local_key": left_key,
                    "current_local_key": right_key,
                    "method": "single-active-speaker-in-shared-audio",
                    "score": 1.0,
                }
            )
            continue
        votes: dict[tuple[str, str], float] = defaultdict(float)
        for left in by_chunk[previous_id]:
            if left["global_end"] < overlap_start or left["global_start"] > overlap_end:
                continue
            for right in by_chunk[current_id]:
                if right["global_end"] < overlap_start or right["global_start"] > overlap_end:
                    continue
                time_overlap = intervals_overlap(left, right)
                if time_overlap <= 0 and abs(left["global_start"] - right["global_start"]) > 2.0:
                    continue
                similarity = difflib.SequenceMatcher(None, normalized(left["text"]), normalized(right["text"])).ratio()
                if similarity >= 0.55:
                    votes[(right["local_key"], left["local_key"])] += similarity + min(time_overlap, 5.0) / 5.0

        candidates_by_right: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for (right_key, left_key), score in votes.items():
            candidates_by_right[right_key].append((score, left_key))
        for right_key, candidates in candidates_by_right.items():
            candidates.sort(reverse=True)
            best_score, left_key = candidates[0]
            second_score = candidates[1][0] if len(candidates) > 1 else 0.0
            if best_score >= 0.8 and best_score >= second_score * 1.25:
                union.union(left_key, right_key)
                links.append(
                    {
                        "previous_chunk": previous_id,
                        "current_chunk": current_id,
                        "previous_local_key": left_key,
                        "current_local_key": right_key,
                        "method": "time-and-text-overlap",
                        "score": best_score,
                    }
                )
    return links


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--vocabulary", default="", help="Proper nouns as transcription hints only")
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow model-network access. Use only after explicit user approval for this model download.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    configure_model_network(args.allow_model_download)
    import mlx.core as mx
    from mlx_audio.stt import load

    manifest_path = args.manifest.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_path = output_dir / "diarization.raw.json"
    if output_path.exists() and not args.overwrite:
        raise SystemExit(f"Output exists; choose another directory or pass --overwrite: {output_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunks = manifest.get("chunks", [])
    if not chunks:
        raise SystemExit("Manifest has no chunks")
    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = output_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    prompt = (
        "Transcribe verbatim with timestamps. Use anonymous speaker labels only, such as S01, S02, S03. "
        "Never infer or output speaker names. Keep labels stable within this audio chunk."
    )
    if args.vocabulary.strip():
        prompt += " Vocabulary hints for spelling only, never identities: " + args.vocabulary.strip()

    load_started = time.perf_counter()
    try:
        model = load(args.model)
    except Exception as exc:
        if not args.allow_model_download:
            raise SystemExit(
                f"Model is unavailable from the offline project cache: {args.model}. "
                "Obtain explicit user approval before retrying with --allow-model-download."
            ) from exc
        raise
    mx.synchronize()
    load_seconds = time.perf_counter() - load_started
    mx.reset_peak_memory()
    inference_total = 0.0
    all_records: list[dict[str, Any]] = []
    chunk_metrics = []

    for chunk in chunks:
        chunk_id = int(chunk["id"])
        started = time.perf_counter()
        result = model.generate(
            chunk["path"],
            max_tokens=args.max_tokens,
            temperature=0.0,
            prompt=prompt,
            prefill_step_size=4096,
        )
        mx.synchronize()
        inference_seconds = time.perf_counter() - started
        inference_total += inference_seconds
        records = local_anonymize(parse_records(result), chunk_id)
        for record in records:
            record["global_start"] = float(chunk["decode_start"]) + record["start"]
            record["global_end"] = float(chunk["decode_start"]) + record["end"]
        all_records.extend(records)
        (chunk_dir / f"chunk-{chunk_id:04d}.json").write_text(
            json.dumps(
                {
                    "chunk": chunk,
                    "warning": "raw labels and generated names are untrusted model output",
                    "text": getattr(result, "text", ""),
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        chunk_metrics.append(
            {
                "chunk_id": chunk_id,
                "inference_seconds": inference_seconds,
                "record_count": len(records),
                "last_local_end": max((r["end"] for r in records), default=0.0),
            }
        )

    union = UnionFind()
    overlap_links = reconcile_overlaps(all_records, chunks, union)
    root_to_global: dict[str, str] = {}
    final_segments = []
    chunk_by_id = {int(chunk["id"]): chunk for chunk in chunks}
    for record in sorted(all_records, key=lambda item: (item["global_start"], item["global_end"])):
        chunk = chunk_by_id[record["chunk_id"]]
        midpoint = (record["global_start"] + record["global_end"]) / 2
        if midpoint < float(chunk["core_start"]) or midpoint >= float(chunk["core_end"]):
            continue
        root = union.find(record["local_key"])
        if root not in root_to_global:
            root_to_global[root] = f"SPK{len(root_to_global) + 1:02d}"
        clipped_start = max(record["global_start"], float(chunk["core_start"]))
        clipped_end = min(record["global_end"], float(chunk["core_end"]))
        if clipped_end <= clipped_start:
            continue
        final_segments.append(
            {
                "id": len(final_segments) + 1,
                "start": clipped_start,
                "end": clipped_end,
                "speaker": root_to_global[root],
                "chunk_id": record["chunk_id"],
                "local_label": record["local_label"],
                "text": record["text"],
            }
        )

    payload = {
        "status": "best-effort anonymous diarization; human verification required",
        "speaker_identity_policy": "No generated label is a verified human identity.",
        "source_manifest": str(manifest_path),
        "model": args.model,
        "model_network_access": args.allow_model_download,
        "prompt": prompt,
        "metrics": {
            "load_seconds": load_seconds,
            "inference_seconds": inference_total,
            "peak_mlx_memory_gb": mx.get_peak_memory() / 1e9,
            "chunk_count": len(chunks),
            "speaker_count": len(root_to_global),
            "segment_count": len(final_segments),
            "overlap_link_count": len(overlap_links),
            "chunks": chunk_metrics,
        },
        "overlap_links": overlap_links,
        "segments": final_segments,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
