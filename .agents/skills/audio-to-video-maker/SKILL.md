---
name: audio-to-video-maker
description: Create editing-ready, verbatim-style transcript handoffs from local audio or video using the project's MLX Whisper workflow. Use when an editor, video maker, subtitle workflow, B-roll planner, or downstream skill needs a matching source audio file, raw SRT, and optional word/token timestamp JSON while preserving fillers, false starts, repetitions, corrections, and uncertainty instead of polished release captions.
---

# Audio to Video Maker

Turn source speech into an evidence-preserving editing handoff. Prioritize the raw spoken record and valid timing over polished prose.

## Create a new run

Work from the repository root. Resolve the input to an absolute path and create a new `outputs/<media-stem>-video-handoff/` directory. Do not overwrite another run.

Require `ffmpeg`, `ffprobe`, and `.venv/bin/python`. Use the preparation and Whisper scripts from the sibling `transcribe-media` skill:

```bash
TRANSCRIBE_SKILL_DIR="$PWD/.agents/skills/transcribe-media"
VIDEO_SKILL_DIR="$PWD/.agents/skills/audio-to-video-maker"
RUN_DIR="$PWD/outputs/<media-stem>-video-handoff"

"$PWD/.venv/bin/python" "$TRANSCRIBE_SKILL_DIR/scripts/prepare_media.py" \
  "/absolute/path/to/input" \
  --output-dir "$RUN_DIR/prepared"

"$PWD/.venv/bin/python" "$TRANSCRIBE_SKILL_DIR/scripts/transcribe_chunks.py" \
  "$RUN_DIR/prepared/manifest.json" \
  --output-dir "$RUN_DIR/whisper" \
  --language zh \
  --word-timestamps
```

Use `--language auto` only when the source language is genuinely unknown. Use prompts only for verified spelling vocabulary; never seed facts, phrases, speaker names, or a cleaned script.

## Preserve the raw speech

Do not run the calibration, translation, diarization, or Markdown publishing stages merely to make this handoff. Do not summarize, rewrite, remove disfluencies, or add editing instructions.

Listen to the beginning, every fallback chunk join, suspicious or low-confidence passages, and the final spoken content. Preserve audible fillers, false starts, repetitions, self-corrections, numbers, units, names, acronyms, and code-switching. Use `[听不清]` only after listening fails to resolve the speech. Retain `[笑]` or `[咳嗽]` only when audible.

Never modify `whisper/transcript.raw.json`; keep it as machine evidence. If listening supports corrections, copy it to `whisper/transcript.verbatim-reviewed.json`, edit only the spoken text, and clear the `words` array for any replacement that no longer has trustworthy token alignment. Pass that reviewed copy to the build step.

Do not add speaker labels to a single-speaker SRT. This handoff format omits speaker labels even when the source has multiple speakers; use the sibling `transcribe-media` skill separately when speaker analysis is required.

## Keep analysis Markdown free of timestamps

Treat Markdown as a content-analysis document, not an editing timeline. If the user also needs Markdown, render it from the intermediate transcript JSON:

```bash
"$PWD/.venv/bin/python" "$TRANSCRIBE_SKILL_DIR/scripts/render_raw_markdown.py" \
  "$RUN_DIR/whisper/transcript.raw.json" \
  --output "$RUN_DIR/delivery/<basename>.content.md"
```

Do not put timecodes into Markdown. Use the SRT as the human-facing timestamped transcript for video cuts. Keep JSON timing only as machine-readable alignment and audit evidence.

## Build the three-file handoff

Run:

```bash
"$PWD/.venv/bin/python" "$VIDEO_SKILL_DIR/scripts/build_editing_handoff.py" \
  "$RUN_DIR/whisper/transcript.raw.json" \
  "$RUN_DIR/prepared/manifest.json" \
  --output-dir "$RUN_DIR/delivery" \
  --language zh-CN
```

The builder keeps input audio in its original container. For video input, it delivers the normalized extracted WAV. It emits:

```text
<basename>.<audio-extension>
<basename>.zh-CN.raw.srt
<basename>.zh-CN.words.json
```

It retains model word timestamps when available, uses them to split long model segments near phrase punctuation, and keeps SRT and JSON segments one-to-one. It never fabricates evenly spaced word times. If word timestamps are unavailable, it emits empty `words` arrays and preserves segment-level SRT timing.

Read [references/output-contract.md](references/output-contract.md) when checking schema details, naming, or the boundary between automated validation and listening review.

## Validate before delivery

Run:

```bash
"$PWD/.venv/bin/python" "$VIDEO_SKILL_DIR/scripts/validate_handoff.py" \
  "$RUN_DIR/delivery/<basename>.zh-CN.raw.srt" \
  "$RUN_DIR/delivery/<basename>.zh-CN.words.json"
```

Treat any validator failure as blocking. Review cue-duration warnings rather than mechanically stretching or merging timestamps. Confirm separately by listening that the final cue is real speech, not a music or silence-tail hallucination, and that no final spoken content is missing.

Report the delivered paths, input duration, first and last cue timestamps, cue count, word timestamp count, model, runtime, chunk warnings, and semantic review limitations. Describe cross-attention/DTW word timing as model-derived alignment, not sample-accurate ground truth.
