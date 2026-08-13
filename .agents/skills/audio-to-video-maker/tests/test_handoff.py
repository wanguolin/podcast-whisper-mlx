#!/usr/bin/env python3
"""Smoke-test deterministic editing handoff construction and validation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class HandoffTest(unittest.TestCase):
    def test_build_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "episode-001.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\0\0" * 8000 * 12)

            words = [
                {"word": "今天", "start": 0.82, "end": 1.28},
                {"word": "我们", "start": 1.34, "end": 1.78},
                {"word": "聊", "start": 1.84, "end": 2.08},
                {"word": "CPI，", "start": 2.14, "end": 2.92},
                {"word": "呃，", "start": 3.16, "end": 3.48},
                {"word": "不是", "start": 3.72, "end": 4.18},
                {"word": "价格", "start": 4.24, "end": 4.70},
                {"word": "下降，", "start": 4.76, "end": 5.34},
                {"word": "而是", "start": 5.62, "end": 6.02},
                {"word": "上涨", "start": 6.08, "end": 6.52},
                {"word": "速度", "start": 6.58, "end": 7.04},
                {"word": "变慢。", "start": 7.10, "end": 7.72},
            ]
            transcript = {
                "source_language": "zh",
                "segments": [
                    {
                        "id": 1,
                        "start": 0.82,
                        "end": 9.4,
                        "text": "今天我们聊 CPI，呃，不是价格下降，而是上涨速度变慢。",
                        "words": words,
                    },
                    {
                        "id": 2,
                        "start": 9.7,
                        "end": 11.2,
                        "text": "这里保留口误。",
                        "words": [],
                    },
                ],
            }
            raw_json = root / "transcript.raw.json"
            raw_json.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
            manifest = {
                "input": {
                    "path": str(source),
                    "media_type": "audio",
                    "duration_seconds": 12.0,
                },
                "normalized_audio": {"path": str(source), "duration_seconds": 12.0},
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            delivery = root / "delivery"

            build = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "build_editing_handoff.py"),
                    str(raw_json),
                    str(manifest_path),
                    "--output-dir",
                    str(delivery),
                    "--language",
                    "zh-CN",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)

            srt = delivery / "episode-001.zh-CN.raw.srt"
            words_json = delivery / "episode-001.zh-CN.words.json"
            self.assertRegex(
                srt.read_text(encoding="utf-8"),
                r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}",
            )
            validate = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "validate_handoff.py"),
                    str(srt),
                    str(words_json),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            payload = json.loads(words_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["audio_file"], "episode-001.wav")
            self.assertEqual(payload["segments"][0]["text"], "今天我们聊CPI，")
            self.assertTrue(any("呃" in segment["text"] for segment in payload["segments"]))
            self.assertTrue(all(segment["end"] <= 12.0 for segment in payload["segments"]))
            self.assertTrue(all(segment["end"] > segment["start"] for segment in payload["segments"]))

    def test_segment_only_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fallback.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\0\0" * 8000 * 10)
            transcript_path = root / "transcript.raw.json"
            transcript_path.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "id": 1,
                                "start": 0.5,
                                "end": 9.5,
                                "text": "没有词级时间戳时仍然保留完整原句。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "input": {
                            "path": str(source),
                            "media_type": "audio",
                            "duration_seconds": 10.0,
                        },
                        "normalized_audio": {
                            "path": str(source),
                            "duration_seconds": 10.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            delivery = root / "delivery"
            build = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "build_editing_handoff.py"),
                    str(transcript_path),
                    str(manifest_path),
                    "--output-dir",
                    str(delivery),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            payload = json.loads(
                (delivery / "fallback.zh-CN.words.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["timestamp_source"], "segment-level-only")
            self.assertEqual(payload["segments"][0]["words"], [])
            self.assertIn("has no usable word timestamps", build.stdout)


if __name__ == "__main__":
    unittest.main()
