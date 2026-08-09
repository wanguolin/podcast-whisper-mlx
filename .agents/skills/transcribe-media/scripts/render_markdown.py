#!/usr/bin/env python3
"""Render calibrated transcript JSON into reviewable Markdown documents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def timestamp(seconds: float) -> str:
    total = max(0, round(seconds * 1000))
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def label(segment: dict[str, Any], speakers: dict[str, str]) -> str:
    speaker_id = str(segment.get("speaker", "UNKNOWN"))
    return speakers.get(speaker_id, speaker_id)


def header(title: str, note: str) -> list[str]:
    return [f"# {title}", "", f"> {note}", ""]


def line(segment: dict[str, Any], text: str, speakers: dict[str, str]) -> str:
    uncertainty = " ⚠️" if segment.get("uncertain") else ""
    return f"[{timestamp(float(segment['start']))}] **{label(segment, speakers)}：** {text}{uncertainty}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewed_json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stem", default="transcript")
    args = parser.parse_args()

    data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    language = str(metadata.get("source_language", "")).lower()
    if language not in {"en", "zh"}:
        raise SystemExit("metadata.source_language must be en or zh")
    segments = data.get("segments", [])
    if not segments:
        raise SystemExit("reviewed transcript has no segments")
    speakers = {
        str(item["id"]): str(item.get("display_name") or item["id"])
        for item in data.get("speakers", [])
    }
    for index, segment in enumerate(segments, start=1):
        if not str(segment.get("calibrated", "")).strip():
            raise SystemExit(f"segment {index} lacks calibrated text")
        if language == "en" and not str(segment.get("zh", "")).strip():
            raise SystemExit(f"English segment {index} lacks zh translation")
        if float(segment["end"]) <= float(segment["start"]):
            raise SystemExit(f"segment {index} has invalid timestamps")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    title = str(metadata.get("title") or args.stem)
    status_note = "机器辅助校准；专名、数字、听不清处和说话人身份仍应人工复核。"

    if language == "en":
        english = header(f"{title} — Calibrated English transcript", status_note)
        chinese = header(f"{title} — 中文译稿", status_note)
        bilingual = header(f"{title} — 中英对照稿", status_note)
        for segment in segments:
            en_text = str(segment["calibrated"]).strip()
            zh_text = str(segment["zh"]).strip()
            english.extend([line(segment, en_text, speakers), ""])
            chinese.extend([line(segment, zh_text, speakers), ""])
            bilingual.extend(
                [
                    f"### {timestamp(float(segment['start']))} · {label(segment, speakers)}",
                    "",
                    en_text,
                    "",
                    zh_text,
                    "",
                ]
            )
        (output_dir / f"{args.stem}.en.md").write_text("\n".join(english) + "\n", encoding="utf-8")
        (output_dir / f"{args.stem}.zh.md").write_text("\n".join(chinese) + "\n", encoding="utf-8")
        (output_dir / f"{args.stem}.bilingual.md").write_text("\n".join(bilingual) + "\n", encoding="utf-8")
    else:
        chinese = header(f"{title} — 中文校准稿", status_note)
        for segment in segments:
            chinese.extend([line(segment, str(segment["calibrated"]).strip(), speakers), ""])
        (output_dir / f"{args.stem}.zh.md").write_text("\n".join(chinese) + "\n", encoding="utf-8")

    notes = header(f"{title} — 复核清单", "只列出仍需确认的内容；不要把推测升级为事实。")
    unresolved = data.get("unresolved", [])
    if unresolved:
        for item in unresolved:
            notes.append(
                f"- `{timestamp(float(item.get('start', 0.0)))}` {item.get('type', '待确认')}：{item.get('note', '')}"
            )
    else:
        notes.append("- 未记录待确认项；这不等于已经完成人工逐句核验。")
    (output_dir / f"{args.stem}.review.md").write_text("\n".join(notes) + "\n", encoding="utf-8")

    created = sorted(str(path) for path in output_dir.glob(f"{args.stem}.*.md"))
    print(json.dumps({"created": created}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
