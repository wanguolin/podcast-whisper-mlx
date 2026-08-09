#!/usr/bin/env python3
"""Assign anonymous diarization labels to timestamped Whisper segments."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("diarization", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    diarization = json.loads(args.diarization.read_text(encoding="utf-8"))
    diarized = diarization.get("segments", [])
    output_segments = []
    unknown = 0
    for segment in transcript.get("segments", []):
        start, end = float(segment["start"]), float(segment["end"])
        votes: dict[str, float] = defaultdict(float)
        for turn in diarized:
            amount = overlap(start, end, float(turn["start"]), float(turn["end"]))
            if amount:
                votes[str(turn["speaker"])] += amount
        if votes:
            speaker, best = max(votes.items(), key=lambda item: item[1])
            confidence = min(1.0, best / max(end - start, 0.001))
        else:
            speaker, confidence = "UNKNOWN", 0.0
            unknown += 1
        output_segments.append({**segment, "speaker": speaker, "speaker_overlap_confidence": confidence})

    payload = {
        **{key: value for key, value in transcript.items() if key != "segments"},
        "status": "machine transcript with best-effort anonymous speakers; calibration required",
        "speaker_labels": True,
        "diarization_source": str(args.diarization),
        "speaker_assignment": {
            "method": "maximum timestamp overlap",
            "unknown_segment_count": unknown,
            "identity_warning": "Anonymous labels are not verified names.",
        },
        "segments": output_segments,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["speaker_assignment"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
