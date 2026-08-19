#!/usr/bin/env python3
"""Route one YouTube video to captions or the local transcription pipeline.

This is the single user-facing YouTube entry for the transcribe-media skill.
It preserves manual captions when available, stops for user input when routing
evidence is weak, and otherwise reuses the existing offline Whisper and MOSS
scripts. Calibration and translation remain active-agent work.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
NEEDS_USER_INPUT = 2
SUPPORTED_LANGUAGES = {"en", "zh"}

sys.path.insert(0, str(SCRIPT_DIR))
from render_raw_markdown import render_content_markdown  # noqa: E402


MULTI_TERMS = (
    "interview",
    "podcast",
    "panel",
    "conversation",
    "debate",
    "roundtable",
    "q&a",
    "host and guest",
    "访谈",
    "采访",
    "对谈",
    "圆桌",
    "播客",
    "嘉宾",
    "辩论",
)
SINGLE_TERMS = (
    "keynote",
    "speech",
    "lecture",
    "monologue",
    "presentation",
    "演讲",
    "讲座",
    "独白",
    "口播",
    "主题分享",
)
TIMING_LINE = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)
MARKUP = re.compile(r"<[^>]+>")


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def base_language(value: Any) -> str | None:
    code = str(value or "").strip().lower().replace("_", "-")
    if code.startswith("zh"):
        return "zh"
    if code.startswith("en"):
        return "en"
    return None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def validate_youtube_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    allowed = (
        hostname == "youtu.be"
        or hostname.endswith(".youtu.be")
        or hostname == "youtube.com"
        or hostname.endswith(".youtube.com")
        or hostname == "youtube-nocookie.com"
        or hostname.endswith(".youtube-nocookie.com")
    )
    if parsed.scheme not in {"http", "https"} or not allowed:
        fail("youtube_to_transcript.py accepts only public YouTube URLs")
    if parsed.username is not None or parsed.password is not None:
        fail("Credentials in YouTube URLs are not supported")


def run(command: Sequence[str], *, description: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        details = (completed.stderr or completed.stdout or "").strip()
        fail(f"{description} failed ({completed.returncode}):\n{details}")
    return completed


def yt_dlp_command(explicit: str | None = None) -> list[str]:
    if explicit:
        resolved = shutil.which(explicit) if "/" not in explicit else explicit
        if not resolved or not Path(resolved).is_file():
            fail(f"yt-dlp executable not found: {explicit}")
        command = [str(resolved)]
    elif found := shutil.which("yt-dlp"):
        command = [found]
    elif uvx := shutil.which("uvx"):
        command = [uvx, "--from", "yt-dlp", "yt-dlp"]
    else:
        fail("yt-dlp is required. Install it or make uvx available on PATH.")

    if deno := shutil.which("deno"):
        command.extend(["--js-runtimes", f"deno:{deno}"])
    elif node := shutil.which("node"):
        command.extend(["--js-runtimes", f"node:{node}"])
    return command


def inspect_video(url: str, command: Sequence[str]) -> dict[str, Any]:
    completed = run(
        [
            *command,
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            url,
        ],
        description="YouTube metadata inspection",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fail(f"yt-dlp returned invalid metadata JSON: {exc}")
    if not isinstance(payload, dict) or not payload.get("id"):
        fail("yt-dlp metadata did not contain a video id")
    return payload


def caption_inventory(captions: Any) -> list[dict[str, Any]]:
    if not isinstance(captions, dict):
        return []
    result = []
    for language, formats in captions.items():
        if language == "live_chat":
            continue
        available = []
        for item in formats if isinstance(formats, list) else []:
            if not isinstance(item, dict):
                continue
            available.append(
                {
                    "ext": item.get("ext"),
                    "name": item.get("name"),
                }
            )
        result.append(
            {
                "language": language,
                "base_language": base_language(language),
                "formats": available,
            }
        )
    return sorted(result, key=lambda item: str(item["language"]))


def safe_metadata(info: dict[str, Any]) -> dict[str, Any]:
    video_id = str(info["id"])
    scalar_fields = (
        "title",
        "description",
        "uploader",
        "uploader_id",
        "channel",
        "channel_id",
        "duration",
        "upload_date",
        "language",
        "categories",
        "tags",
        "availability",
        "live_status",
    )
    payload = {key: info.get(key) for key in scalar_fields}
    payload.update(
        {
            "schema_version": 1,
            "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "extractor": "yt-dlp",
            "id": video_id,
            "webpage_url": canonical_url(video_id),
            "chapters": [
                {
                    "start_time": item.get("start_time"),
                    "end_time": item.get("end_time"),
                    "title": item.get("title"),
                }
                for item in info.get("chapters", [])
                if isinstance(item, dict)
            ],
            "manual_captions": caption_inventory(info.get("subtitles")),
            "automatic_captions": caption_inventory(info.get("automatic_captions")),
            "url_policy": "Only the canonical video URL is retained; signed media URLs are omitted.",
        }
    )
    return payload


def original_automatic_languages(info: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    captions = info.get("automatic_captions")
    if not isinstance(captions, dict):
        return result
    for key, formats in captions.items():
        if str(key).lower().endswith("-orig"):
            if language := base_language(str(key)[:-5]):
                result.add(language)
        for item in formats if isinstance(formats, list) else []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(str(item["url"])).query)
            if query.get("tlang"):
                continue
            for code in query.get("lang", []):
                if language := base_language(code):
                    result.add(language)
    return result


def text_language_signal(text: str, *, minimum_latin: int, minimum_han: int) -> str | None:
    han = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if han >= minimum_han and han >= latin * 0.3:
        return "zh"
    if latin >= minimum_latin and han <= max(3, latin * 0.02):
        return "en"
    return None


def infer_language(info: dict[str, Any], requested: str) -> dict[str, str]:
    if requested in SUPPORTED_LANGUAGES:
        return {"value": requested, "confidence": "high", "evidence": "user option"}
    if language := base_language(info.get("language")):
        return {"value": language, "confidence": "high", "evidence": "YouTube language metadata"}
    manual_languages = {
        base_language(code)
        for code in (info.get("subtitles") or {})
        if code != "live_chat" and base_language(code) in SUPPORTED_LANGUAGES
    }
    if len(manual_languages) == 1:
        return {
            "value": next(iter(manual_languages)),
            "confidence": "high",
            "evidence": "manual YouTube caption track inventory",
        }
    automatic = original_automatic_languages(info)
    if len(automatic) == 1:
        return {
            "value": next(iter(automatic)),
            "confidence": "high",
            "evidence": "original YouTube automatic-caption track",
        }
    if len(automatic) > 1:
        return {
            "value": "unknown",
            "confidence": "low",
            "evidence": "multiple possible original automatic-caption languages",
        }

    title = str(info.get("title") or "")
    body = " ".join(
        [
            str(info.get("description") or ""),
            " ".join(str(item) for item in info.get("tags", []) or []),
            " ".join(str(item) for item in info.get("categories", []) or []),
        ]
    )
    title_signal = text_language_signal(title, minimum_latin=20, minimum_han=8)
    body_signal = text_language_signal(body, minimum_latin=100, minimum_han=30)
    if title_signal and title_signal == body_signal:
        return {
            "value": title_signal,
            "confidence": "high",
            "evidence": "title and description independently indicate the same language",
        }
    if title_signal or body_signal:
        return {
            "value": title_signal or body_signal or "unknown",
            "confidence": "medium",
            "evidence": "page text heuristic only",
        }
    return {"value": "unknown", "confidence": "low", "evidence": "no reliable language signal"}


def term_matches(text: str, terms: Sequence[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term in lowered]


def infer_conversation(
    info: dict[str, Any], requested: str, content_type: str
) -> dict[str, str]:
    if requested in {"single", "multi"}:
        return {"value": requested, "confidence": "high", "evidence": "user option"}
    if content_type:
        multi = term_matches(content_type, MULTI_TERMS)
        single = term_matches(content_type, SINGLE_TERMS)
        if multi and not single:
            return {
                "value": "multi",
                "confidence": "high",
                "evidence": f"user content type: {', '.join(multi)}",
            }
        if single and not multi:
            return {
                "value": "single",
                "confidence": "high",
                "evidence": f"user content type: {', '.join(single)}",
            }

    title = str(info.get("title") or "")
    title_multi = term_matches(title, MULTI_TERMS)
    title_single = term_matches(title, SINGLE_TERMS)
    if title_multi and not title_single:
        return {
            "value": "multi",
            "confidence": "high",
            "evidence": f"explicit title terms: {', '.join(title_multi)}",
        }
    if title_single and not title_multi:
        return {
            "value": "single",
            "confidence": "high",
            "evidence": f"explicit title terms: {', '.join(title_single)}",
        }

    body = " ".join(
        [
            str(info.get("description") or ""),
            " ".join(str(item) for item in info.get("tags", []) or []),
        ]
    )
    body_multi = term_matches(body, MULTI_TERMS)
    body_single = term_matches(body, SINGLE_TERMS)
    if body_multi and not body_single:
        return {
            "value": "multi",
            "confidence": "medium",
            "evidence": f"description terms: {', '.join(body_multi)}",
        }
    if body_single and not body_multi:
        return {
            "value": "single",
            "confidence": "medium",
            "evidence": f"description terms: {', '.join(body_single)}",
        }
    return {
        "value": "unknown",
        "confidence": "low",
        "evidence": "page metadata does not establish the number of speakers",
    }


def preferred_manual_caption(
    info: dict[str, Any], requested_language: str, inferred_language: str | None
) -> tuple[str, str] | None:
    captions = info.get("subtitles")
    if not isinstance(captions, dict):
        return None
    supported = [
        str(code)
        for code in captions
        if code != "live_chat" and base_language(code) in SUPPORTED_LANGUAGES
    ]
    if not supported:
        return None
    target = requested_language if requested_language in SUPPORTED_LANGUAGES else inferred_language
    if target not in SUPPORTED_LANGUAGES:
        bases = {base_language(code) for code in supported}
        if len(bases) != 1:
            return None
        target = next(iter(bases))
    matches = [code for code in supported if base_language(code) == target]
    if not matches:
        return None
    matches.sort(key=lambda code: (code.lower() != target, "orig" not in code.lower(), len(code), code))
    return matches[0], str(target)


def plan_route(
    info: dict[str, Any],
    *,
    language: str,
    conversation_mode: str,
    content_type: str,
    allow_manual_captions: bool = True,
) -> dict[str, Any]:
    language_signal = infer_language(info, language)
    manual = (
        preferred_manual_caption(
            info,
            language,
            language_signal["value"] if language_signal["confidence"] == "high" else None,
        )
        if allow_manual_captions
        else None
    )
    if manual:
        caption_language, source_language = manual
        conversation = infer_conversation(info, conversation_mode, content_type)
        run_diarization = (
            conversation["confidence"] == "high" and conversation["value"] == "multi"
        )
        return {
            "status": "ready",
            "transcript_source": "youtube_manual_caption",
            "caption_language": caption_language,
            "source_language": source_language,
            "language_evidence": {
                "value": source_language,
                "confidence": "high",
                "evidence": f"selected manual caption track {caption_language}",
            },
            "conversation": conversation,
            "run_whisper": False,
            "run_diarization": run_diarization,
            "questions": [],
        }

    questions = []
    if language_signal["confidence"] != "high" or language_signal["value"] not in SUPPORTED_LANGUAGES:
        questions.append(
            {
                "field": "language",
                "question": "Is the spoken source language Chinese or English?",
                "rerun_with": "--language zh|en",
                "current_inference": language_signal,
            }
        )
    conversation = infer_conversation(info, conversation_mode, content_type)
    if conversation["confidence"] != "high" or conversation["value"] not in {"single", "multi"}:
        questions.append(
            {
                "field": "conversation_mode",
                "question": "Is this primarily one speaker or a multi-speaker conversation?",
                "rerun_with": "--conversation-mode single|multi",
                "current_inference": conversation,
            }
        )
    if questions:
        return {
            "status": "needs_user_input",
            "transcript_source": "local_whisper",
            "source_language": language_signal["value"],
            "language_evidence": language_signal,
            "conversation": conversation,
            "run_whisper": False,
            "run_diarization": False,
            "questions": questions,
        }
    return {
        "status": "ready",
        "transcript_source": "local_whisper",
        "source_language": language_signal["value"],
        "language_evidence": language_signal,
        "conversation": conversation,
        "run_whisper": True,
        "run_diarization": conversation["value"] == "multi",
        "questions": [],
    }


def parse_clock(value: str) -> float:
    normalized = value.replace(",", ".")
    pieces = normalized.split(":")
    if len(pieces) == 2:
        hours = 0
        minutes, seconds = pieces
    elif len(pieces) == 3:
        hours, minutes, seconds = pieces
    else:
        fail(f"Unsupported caption timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clean_caption_text(value: str) -> str:
    text = html.unescape(MARKUP.sub("", value))
    return " ".join(text.replace("\u200b", "").split()).strip()


def deduplicate_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in sorted(segments, key=lambda row: (row["start"], row["end"])):
        text = clean_caption_text(str(item.get("text") or ""))
        start, end = float(item["start"]), float(item["end"])
        if not text or end <= start:
            continue
        if result and result[-1]["text"] == text and start <= result[-1]["end"] + 0.05:
            result[-1]["end"] = max(result[-1]["end"], end)
            continue
        result.append({"id": len(result) + 1, "start": start, "end": end, "text": text})
    return result


def parse_json3(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for event in data.get("events", []):
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue
        text = "".join(str(item.get("utf8") or "") for item in event["segs"] if isinstance(item, dict))
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        if duration <= 0:
            duration = 0.001
        segments.append({"start": start, "end": start + duration, "text": text})
    return deduplicate_segments(segments)


def parse_timed_text(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig"))
    segments = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if TIMING_LINE.search(line)), None)
        if timing_index is None:
            continue
        match = TIMING_LINE.search(lines[timing_index])
        assert match
        text = " ".join(lines[timing_index + 1 :])
        segments.append(
            {
                "start": parse_clock(match.group("start")),
                "end": parse_clock(match.group("end")),
                "text": text,
            }
        )
    return deduplicate_segments(segments)


def parse_caption(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json3":
        segments = parse_json3(path)
    elif path.suffix.lower() in {".vtt", ".srt"}:
        segments = parse_timed_text(path)
    else:
        fail(f"Unsupported downloaded caption format: {path.suffix}")
    if not segments:
        fail(f"Downloaded caption contains no usable timed text: {path}")
    return segments


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_or_validate_metadata(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"Existing YouTube metadata is invalid: {path}: {exc}")
        if str(existing.get("id")) != str(payload.get("id")):
            fail(
                "The output directory belongs to a different YouTube video; "
                "choose a new --output-dir."
            )
    write_json(path, payload)


def download_manual_caption(
    url: str,
    video_id: str,
    language: str,
    source_dir: Path,
    command: Sequence[str],
) -> Path:
    captions_dir = source_dir / "captions"
    captions_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        path
        for path in captions_dir.glob(f"{video_id}.{language}.*")
        if path.suffix.lower() in {".json3", ".vtt", ".srt"}
    )
    if existing:
        return existing[0]
    run(
        [
            *command,
            "--no-playlist",
            "--skip-download",
            "--write-subs",
            "--sub-langs",
            language,
            "--sub-format",
            "json3/vtt/srt/best",
            "--paths",
            str(captions_dir),
            "--output",
            f"{video_id}.%(ext)s",
            url,
        ],
        description="YouTube manual-caption download",
    )
    matches = sorted(
        path
        for path in captions_dir.glob(f"{video_id}.*")
        if path.suffix.lower() in {".json3", ".vtt", ".srt"}
    )
    if not matches:
        fail("yt-dlp reported success but no manual-caption file was created")
    preferred = [path for path in matches if f".{language}." in path.name]
    return (preferred or matches)[0]


def write_caption_raw(
    caption_path: Path,
    output_dir: Path,
    metadata_path: Path,
    video_id: str,
    language: str,
) -> Path:
    raw_path = output_dir / "transcript.raw.json"
    if raw_path.exists():
        return raw_path
    segments = parse_caption(caption_path)
    payload = {
        "status": "YouTube manual caption; active-agent calibration required",
        "speaker_labels": False,
        "source_type": "youtube_manual_caption",
        "source_manifest": str(metadata_path),
        "source_file": str(caption_path),
        "source_file_sha256": sha256(caption_path),
        "source_language": language,
        "video_id": video_id,
        "segments": segments,
    }
    write_json(raw_path, payload)
    (output_dir / "transcript.raw.md").write_text(
        render_content_markdown(payload), encoding="utf-8"
    )
    return raw_path


def validate_caption_coverage(raw_path: Path, media_duration: Any) -> dict[str, Any]:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    if not segments:
        fail(f"Caption transcript has no segments: {raw_path}")
    first_start = min(float(item["start"]) for item in segments)
    last_end = max(float(item["end"]) for item in segments)
    try:
        duration = float(media_duration)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return {
            "status": "duration_unknown",
            "first_start": first_start,
            "last_end": last_end,
            "media_duration": None,
            "requires_user_input": False,
        }
    allowance = min(60.0, max(10.0, duration * 0.1))
    leading_gap = max(0.0, first_start)
    trailing_gap = max(0.0, duration - last_end)
    suspicious = leading_gap > allowance or trailing_gap > allowance
    return {
        "status": "suspicious_partial" if suspicious else "plausibly_complete",
        "first_start": first_start,
        "last_end": last_end,
        "media_duration": duration,
        "leading_gap": leading_gap,
        "trailing_gap": trailing_gap,
        "gap_allowance": allowance,
        "requires_user_input": suspicious,
    }


def download_best_audio(
    url: str, video_id: str, source_dir: Path, command: Sequence[str]
) -> Path:
    existing = sorted(
        path
        for path in source_dir.glob(f"{video_id}.source.*")
        if path.is_file() and not path.name.endswith(".info.json") and path.suffix != ".part"
    )
    if existing:
        return existing[0]
    source_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            *command,
            "--no-playlist",
            "--format",
            "bestaudio/best",
            "--write-info-json",
            "--paths",
            str(source_dir),
            "--output",
            f"{video_id}.source.%(ext)s",
            url,
        ],
        description="YouTube best-audio download",
    )
    matches = sorted(
        path
        for path in source_dir.glob(f"{video_id}.source.*")
        if path.is_file() and not path.name.endswith(".info.json") and path.suffix != ".part"
    )
    if not matches:
        fail("yt-dlp reported success but no audio file was created")
    return matches[0]


def run_internal(command: Sequence[str], description: str) -> None:
    run([sys.executable, *command], description=description)


def prepare_audio(audio_path: Path, run_dir: Path) -> Path:
    manifest = run_dir / "prepared" / "manifest.json"
    if not manifest.exists():
        run_internal(
            [
                str(SCRIPT_DIR / "prepare_media.py"),
                str(audio_path),
                "--output-dir",
                str(run_dir / "prepared"),
            ],
            "audio preparation",
        )
    return manifest


def transcribe_audio(
    manifest: Path,
    run_dir: Path,
    language: str,
    vocabulary_file: Path | None,
    word_timestamps: bool,
) -> Path:
    transcript = run_dir / "whisper" / "transcript.raw.json"
    if transcript.exists():
        return transcript
    command = [
        str(SCRIPT_DIR / "transcribe_chunks.py"),
        str(manifest),
        "--output-dir",
        str(run_dir / "whisper"),
        "--language",
        language,
    ]
    if vocabulary_file:
        command.extend(["--prompt-file", str(vocabulary_file.expanduser().resolve())])
    if word_timestamps:
        command.append("--word-timestamps")
    run_internal(command, "offline Whisper transcription")
    return transcript


def add_speakers(
    transcript: Path,
    manifest: Path,
    run_dir: Path,
    vocabulary_file: Path | None,
) -> Path:
    diarization = run_dir / "moss" / "diarization.raw.json"
    if not diarization.exists():
        command = [
            str(SCRIPT_DIR / "diarize_chunks.py"),
            str(manifest),
            "--output-dir",
            str(run_dir / "moss"),
        ]
        if vocabulary_file:
            vocabulary = vocabulary_file.expanduser().resolve().read_text(encoding="utf-8").strip()
            if vocabulary:
                command.extend(["--vocabulary", vocabulary[:4000]])
        run_internal(command, "offline anonymous-speaker diarization")
    assigned = run_dir / "transcript.speakers.json"
    if not assigned.exists():
        run_internal(
            [
                str(SCRIPT_DIR / "assign_speakers.py"),
                str(transcript),
                str(diarization),
                "--output",
                str(assigned),
            ],
            "speaker assignment",
        )
    return assigned


def required_deliverables(language: str) -> list[str]:
    if language == "en":
        return [
            "final/transcript.content.md",
            "final/transcript.en.md",
            "final/transcript.zh.md",
            "final/transcript.bilingual.md",
            "final/transcript.review.md",
        ]
    return [
        "final/transcript.content.md",
        "final/transcript.zh.md",
        "final/transcript.review.md",
    ]


def render_if_reviewed(run_dir: Path) -> dict[str, Any] | None:
    reviewed = run_dir / "transcript.reviewed.json"
    if not reviewed.exists():
        return None
    run_internal(
        [
            str(SCRIPT_DIR / "render_markdown.py"),
            str(reviewed),
            "--output-dir",
            str(run_dir / "final"),
            "--stem",
            "transcript",
        ],
        "final Markdown rendering",
    )
    return {
        "status": "complete",
        "run_dir": str(run_dir),
        "reviewed_json": str(reviewed),
        "final_dir": str(run_dir / "final"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Route one YouTube video to manual captions or offline local transcription."
    )
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument(
        "--conversation-mode", choices=("auto", "single", "multi"), default="auto"
    )
    parser.add_argument(
        "--content-type",
        default="",
        help="User-provided description such as keynote, interview, panel, or lecture.",
    )
    parser.add_argument(
        "--caption-policy",
        choices=("auto", "accept", "whisper"),
        default="auto",
        help=(
            "Use manual captions by default; accept overrides a suspicious coverage warning, "
            "while whisper preserves the caption but routes primary text through local ASR."
        ),
    )
    parser.add_argument(
        "--vocabulary-file",
        type=Path,
        help="Verified spelling hints only; never use unverified page text as ASR prompt data.",
    )
    parser.add_argument("--word-timestamps", action="store_true")
    parser.add_argument("--yt-dlp-bin", help=argparse.SUPPRESS)
    args = parser.parse_args()

    validate_youtube_url(args.url)
    command = yt_dlp_command(args.yt_dlp_bin)
    info = inspect_video(args.url, command)
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if extractor and "youtube" not in extractor:
        fail(f"yt-dlp resolved a non-YouTube extractor: {extractor}")
    video_id = str(info["id"])
    run_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else (PROJECT_ROOT / "outputs" / f"youtube-{video_id}-transcript").resolve()
    )
    source_dir = run_dir / "source"
    metadata_path = source_dir / "youtube.info.json"
    source_dir.mkdir(parents=True, exist_ok=True)
    write_or_validate_metadata(metadata_path, safe_metadata(info))

    if completed := render_if_reviewed(run_dir):
        print(json.dumps(completed, ensure_ascii=False, indent=2))
        return

    plan = plan_route(
        info,
        language=args.language,
        conversation_mode=args.conversation_mode,
        content_type=args.content_type,
        allow_manual_captions=args.caption_policy != "whisper",
    )
    plan.update(
        {
            "schema_version": 1,
            "video_id": video_id,
            "title": info.get("title"),
            "canonical_url": canonical_url(video_id),
            "run_dir": str(run_dir),
            "content_type": args.content_type or None,
            "model_downloads_allowed": False,
        }
    )
    plan_path = run_dir / "youtube.route.json"
    write_json(plan_path, plan)
    if plan["status"] == "needs_user_input":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        raise SystemExit(NEEDS_USER_INPUT)

    language = str(plan["source_language"])
    if language not in SUPPORTED_LANGUAGES:
        fail("Only Chinese and English source routing is currently supported")

    if plan["transcript_source"] == "youtube_manual_caption":
        caption_path = download_manual_caption(
            args.url,
            video_id,
            str(plan["caption_language"]),
            source_dir,
            command,
        )
        transcript = write_caption_raw(
            caption_path,
            run_dir / "caption",
            metadata_path,
            video_id,
            language,
        )
        caption_validation = validate_caption_coverage(transcript, info.get("duration"))
        plan["caption_validation"] = caption_validation
        write_json(plan_path, plan)
        if caption_validation["requires_user_input"] and args.caption_policy == "auto":
            plan["status"] = "needs_user_input"
            plan["questions"] = [
                {
                    "field": "caption_policy",
                    "question": (
                        "The manual caption does not plausibly cover the full video. "
                        "Should the workflow accept it or use local Whisper as primary text?"
                    ),
                    "rerun_with": "--caption-policy accept|whisper",
                    "current_inference": caption_validation,
                }
            ]
            write_json(plan_path, plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            raise SystemExit(NEEDS_USER_INPUT)
    else:
        audio_path = download_best_audio(args.url, video_id, source_dir, command)
        manifest = prepare_audio(audio_path, run_dir)
        transcript = transcribe_audio(
            manifest,
            run_dir,
            language,
            args.vocabulary_file,
            args.word_timestamps,
        )

    speakers_path = None
    if plan["run_diarization"]:
        audio_path = download_best_audio(args.url, video_id, source_dir, command)
        manifest = prepare_audio(audio_path, run_dir)
        speakers_path = add_speakers(
            transcript,
            manifest,
            run_dir,
            args.vocabulary_file,
        )

    handoff = {
        "status": "ready_for_agent_review",
        "video_id": video_id,
        "title": info.get("title"),
        "run_dir": str(run_dir),
        "route": str(plan_path),
        "metadata": str(metadata_path),
        "raw_transcript": str(transcript),
        "speaker_transcript": str(speakers_path) if speakers_path else None,
        "source_language": language,
        "transcript_source": plan["transcript_source"],
        "caption_validation": plan.get("caption_validation"),
        "reviewed_json_to_create": str(run_dir / "transcript.reviewed.json"),
        "calibration_protocol": str(
            SCRIPT_DIR.parent / "references" / "calibration-protocol.md"
        ),
        "required_deliverables": required_deliverables(language),
        "next_action": (
            "The active agent must calibrate the raw evidence, translate English to Chinese when "
            "applicable, create transcript.reviewed.json, then rerun this same command to render "
            "the final Markdown. Do not invoke another language or translation model."
        ),
    }
    handoff_path = run_dir / "agent-handoff.json"
    write_json(handoff_path, handoff)
    print(json.dumps(handoff, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
