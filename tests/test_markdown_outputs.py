from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_RENDERER = ROOT / ".agents/skills/transcribe-media/scripts/render_raw_markdown.py"
FINAL_RENDERER = ROOT / ".agents/skills/transcribe-media/scripts/render_markdown.py"
MOSS_REVIEW_BUILDER = ROOT / "scripts/build_review_transcript.py"
WHISPER_REVIEW_BUILDER = ROOT / "scripts/build_whisper_review_transcript.py"
TIMECODE = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d{3})?\b")


def load_raw_renderer():
    spec = importlib.util.spec_from_file_location("render_raw_markdown", RAW_RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MarkdownOutputTests(unittest.TestCase):
    def test_raw_markdown_contains_only_spoken_content(self) -> None:
        renderer = load_raw_renderer()
        markdown = renderer.render_content_markdown(
            {
                "segments": [
                    {"start": 65.25, "end": 68.5, "text": "First paragraph."},
                    {"start": 3700.0, "end": 3704.0, "text": "Second paragraph."},
                ]
            }
        )
        self.assertEqual(markdown, "First paragraph.\n\nSecond paragraph.\n")
        self.assertNotRegex(markdown, TIMECODE)

    def test_final_markdown_has_content_but_no_timecodes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewed = root / "reviewed.json"
            reviewed.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Example interview",
                            "source_file": "/tmp/example.wav",
                            "source_language": "en",
                        },
                        "speakers": [
                            {
                                "id": "SPK01",
                                "display_name": "Host",
                                "confidence": "unknown",
                                "evidence": "Anonymous label.",
                            }
                        ],
                        "segments": [
                            {
                                "start": 65.25,
                                "end": 68.5,
                                "speaker": "SPK01",
                                "source": "Original sentence.",
                                "calibrated": "Corrected sentence.",
                                "zh": "校准后的句子。",
                                "uncertain": True,
                            }
                        ],
                        "unresolved": [
                            {"start": 65.25, "type": "数字待核", "note": "Confirm the figure."}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_dir = root / "final"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FINAL_RENDERER),
                    str(reviewed),
                    "--output-dir",
                    str(output_dir),
                    "--stem",
                    "transcript",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            markdown_files = sorted(output_dir.glob("*.md"))
            self.assertEqual(len(markdown_files), 5)
            combined = "\n".join(path.read_text(encoding="utf-8") for path in markdown_files)
            self.assertIn("Corrected sentence.", combined)
            self.assertIn("校准后的句子。", combined)
            self.assertIn("Confirm the figure.", combined)
            self.assertNotRegex(combined, TIMECODE)

    def test_review_builders_keep_timecodes_out_of_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                (
                    WHISPER_REVIEW_BUILDER,
                    {
                        "segments": [
                            {"start": 65.25, "end": 68.5, "text": "Whisper content."}
                        ]
                    },
                    ["--cutoff", "100"],
                    "whisper",
                ),
                (
                    MOSS_REVIEW_BUILDER,
                    {
                        "segments": [
                            {
                                "start": 65.25,
                                "end": 68.5,
                                "speaker_id": "S01",
                                "text": "[S01] MOSS content.",
                            }
                        ]
                    },
                    ["--speech-cutoff", "100", "--first-segment-start", "65.25"],
                    "moss",
                ),
            ]
            for script, payload, options, stem in cases:
                with self.subTest(builder=script.name):
                    source = root / f"{stem}.json"
                    prefix = root / f"{stem}-review"
                    source.write_text(json.dumps(payload), encoding="utf-8")
                    completed = subprocess.run(
                        [sys.executable, str(script), str(source), str(prefix), *options],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    markdown = prefix.with_suffix(".md").read_text(encoding="utf-8")
                    srt = prefix.with_suffix(".srt").read_text(encoding="utf-8")
                    self.assertNotRegex(markdown, TIMECODE)
                    self.assertRegex(srt, TIMECODE)


if __name__ == "__main__":
    unittest.main()
