from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/youtube_to_transcript.py"
SPEC = importlib.util.spec_from_file_location("youtube_to_transcript", SCRIPT)
assert SPEC and SPEC.loader
youtube = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(youtube)


class YouTubeToTranscriptTests(unittest.TestCase):
    def make_fake_ytdlp(self, root: Path, metadata: dict[str, object]) -> Path:
        script = root / "fake-yt-dlp"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"metadata = {metadata!r}\n"
            "if '--dump-single-json' in sys.argv:\n"
            "    print(json.dumps(metadata))\n"
            "elif '--write-subs' in sys.argv:\n"
            "    target = pathlib.Path(sys.argv[sys.argv.index('--paths') + 1])\n"
            "    target.mkdir(parents=True, exist_ok=True)\n"
            "    language = sys.argv[sys.argv.index('--sub-langs') + 1]\n"
            "    payload = {'events': [{'tStartMs': 0, 'dDurationMs': 1000, "
            "'segs': [{'utf8': 'Hello from captions.'}]}]}\n"
            "    (target / f\"{metadata['id']}.{language}.json3\").write_text(json.dumps(payload))\n"
            "else:\n"
            "    raise SystemExit('unexpected fake yt-dlp invocation')\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

    def test_safe_metadata_omits_signed_urls_and_format_payloads(self) -> None:
        payload = youtube.safe_metadata(
            {
                "id": "abc123",
                "title": "Example",
                "webpage_url": "https://www.youtube.com/watch?v=abc123&token=secret",
                "formats": [{"url": "https://signed.example/media?token=secret"}],
                "subtitles": {
                    "en": [
                        {
                            "ext": "json3",
                            "url": "https://www.youtube.com/api/timedtext?signature=secret",
                        }
                    ]
                },
            }
        )
        encoded = json.dumps(payload)
        self.assertEqual(payload["webpage_url"], "https://www.youtube.com/watch?v=abc123")
        self.assertNotIn("secret", encoded)
        self.assertNotIn("formats", payload)

    def test_non_youtube_url_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            youtube.validate_youtube_url("https://example.com/watch?v=abc123")

    def test_output_directory_cannot_switch_video_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "youtube.info.json"
            youtube.write_json(path, {"id": "first"})
            with self.assertRaises(SystemExit):
                youtube.write_or_validate_metadata(path, {"id": "second"})

    def test_original_automatic_caption_language_ignores_translations(self) -> None:
        languages = youtube.original_automatic_languages(
            {
                "automatic_captions": {
                    "en-orig": [
                        {
                            "url": "https://www.youtube.com/api/timedtext?kind=asr&lang=en"
                        }
                    ],
                    "zh-Hans": [
                        {
                            "url": "https://www.youtube.com/api/timedtext?kind=asr&lang=en&tlang=zh-Hans"
                        }
                    ],
                }
            }
        )
        self.assertEqual(languages, {"en"})

    def test_manual_caption_multi_route_skips_whisper_but_adds_speakers(self) -> None:
        plan = youtube.plan_route(
            {
                "id": "abc123",
                "title": "An interview",
                "subtitles": {"en": [{"ext": "json3"}]},
            },
            language="auto",
            conversation_mode="auto",
            content_type="",
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["transcript_source"], "youtube_manual_caption")
        self.assertFalse(plan["run_whisper"])
        self.assertTrue(plan["run_diarization"])

    def test_manual_caption_single_route_skips_all_speech_models(self) -> None:
        plan = youtube.plan_route(
            {
                "id": "abc123",
                "title": "A technical lecture",
                "subtitles": {"en": [{"ext": "json3"}]},
            },
            language="auto",
            conversation_mode="auto",
            content_type="",
        )
        self.assertFalse(plan["run_whisper"])
        self.assertFalse(plan["run_diarization"])

    def test_no_caption_and_weak_metadata_requires_user_input(self) -> None:
        plan = youtube.plan_route(
            {"id": "abc123", "title": "Episode 7"},
            language="auto",
            conversation_mode="auto",
            content_type="",
        )
        self.assertEqual(plan["status"], "needs_user_input")
        self.assertEqual(
            {item["field"] for item in plan["questions"]},
            {"language", "conversation_mode"},
        )
        self.assertFalse(plan["run_whisper"])

    def test_user_multi_route_runs_whisper_and_diarization(self) -> None:
        plan = youtube.plan_route(
            {"id": "abc123", "title": "Episode 7"},
            language="en",
            conversation_mode="multi",
            content_type="interview",
        )
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["run_whisper"])
        self.assertTrue(plan["run_diarization"])

    def test_json3_caption_parser_preserves_timing_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "caption.json3"
            path.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "tStartMs": 1250,
                                "dDurationMs": 2000,
                                "segs": [{"utf8": "Hello "}, {"utf8": "world."}],
                            },
                            {
                                "tStartMs": 4000,
                                "dDurationMs": 1500,
                                "segs": [{"utf8": "Second line."}],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            segments = youtube.parse_caption(path)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["text"], "Hello world.")
        self.assertEqual(segments[0]["start"], 1.25)
        self.assertEqual(segments[0]["end"], 3.25)

    def test_vtt_caption_parser_removes_markup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "caption.vtt"
            path.write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<c.host>Hello</c> &amp; welcome.\n",
                encoding="utf-8",
            )
            segments = youtube.parse_caption(path)
        self.assertEqual(segments[0]["text"], "Hello & welcome.")

    def test_caption_coverage_flags_material_missing_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "transcript.raw.json"
            youtube.write_json(
                raw,
                {"segments": [{"start": 0.0, "end": 30.0, "text": "Partial."}]},
            )
            validation = youtube.validate_caption_coverage(raw, 300.0)
        self.assertEqual(validation["status"], "suspicious_partial")
        self.assertTrue(validation["requires_user_input"])

    def test_cli_stops_before_download_when_route_is_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = self.make_fake_ytdlp(root, {"id": "uncertain", "title": "Episode 7"})
            run_dir = root / "run"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "https://www.youtube.com/watch?v=uncertain",
                    "--output-dir",
                    str(run_dir),
                    "--yt-dlp-bin",
                    str(fake),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, youtube.NEEDS_USER_INPUT)
            self.assertEqual(payload["status"], "needs_user_input")
            self.assertTrue((run_dir / "youtube.route.json").is_file())
            self.assertFalse(any((run_dir / "source").glob("*.source.*")))

    def test_cli_uses_manual_caption_and_writes_agent_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = self.make_fake_ytdlp(
                root,
                {
                    "id": "captioned",
                    "title": "A technical lecture",
                    "subtitles": {"en": [{"ext": "json3"}]},
                },
            )
            run_dir = root / "run"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "https://www.youtube.com/watch?v=captioned",
                    "--output-dir",
                    str(run_dir),
                    "--yt-dlp-bin",
                    str(fake),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(payload["status"], "ready_for_agent_review")
            self.assertEqual(payload["transcript_source"], "youtube_manual_caption")
            self.assertTrue((run_dir / "caption" / "transcript.raw.json").is_file())
            self.assertTrue((run_dir / "agent-handoff.json").is_file())
            self.assertFalse((run_dir / "whisper").exists())


if __name__ == "__main__":
    unittest.main()
