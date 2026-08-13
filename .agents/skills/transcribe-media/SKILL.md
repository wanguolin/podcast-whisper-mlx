---
name: transcribe-media
description: Download remote media URLs or process local audio and video into calibrated, reviewable Markdown using the project's MLX transcription environment. Use for podcast or direct media URLs, audio/video transcription, video audio extraction, long-recording silence-aware chunking, Chinese transcript correction, English transcript correction followed by Chinese translation, bilingual transcript delivery, or best-effort speaker separation in this project.
---

# Transcribe Media

Produce evidence-preserving transcripts from remote or local media. Prefer reliable text over confident-looking speaker names.

## Set paths and resolve the input

Run from the project directory:

```bash
TRANSCRIBE_SKILL_DIR="$PWD/.agents/skills/transcribe-media"
TRANSCRIBE_PYTHON="$PWD/.venv/bin/python"
```

Require `ffmpeg`, `ffprobe`, and the project `.venv` with `mlx-audio`. Put each run in a new `outputs/<media-stem>-transcript/` directory; do not overwrite unrelated prior runs.

For an `http://` or `https://` audio/video URL, download it before preparation:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/download_media.py" \
  "https://example.com/episode.mp3" \
  --output-dir "outputs/<media-stem>-transcript/source"
```

Read the returned `path` from stdout or `source/download.json` and use that absolute local path below. The downloader follows validated HTTP(S) redirects, rejects private/local hosts by default, refuses overwrite, limits the response to 4 GiB, records a SHA-256 hash, and redacts URL queries from its manifest. Use `--allow-private-hosts` only for an explicitly authorized internal or local source. If a webpage URL does not return media bytes, resolve the page to a direct media URL with an appropriate site-specific downloader before continuing.

For local input, resolve the file to an absolute path and continue directly.

## Prepare audio and silence-aware chunks

Run:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/prepare_media.py" \
  "/absolute/path/to/input" \
  --output-dir "outputs/<media-stem>-transcript/prepared"
```

This command detects the downloaded or local media type with `ffprobe`. For video, it extracts the first audio stream; for all supported inputs, it converts the audio to Whisper-compatible 16 kHz mono PCM and writes a manifest. Media longer than 30 minutes is split near the midpoint of long silences. Each chunk has an 8-second decoded overlap but a non-overlapping core interval.

Keep the defaults unless the recording demands otherwise:

- Speech with few pauses: lower `--silence-min-seconds` to `0.5`, but inspect joins.
- Noisy rooms: lower `--silence-noise-db` cautiously, for example `-40`.
- Dense panel discussions: prefer 20–25 minute targets, never chunks longer than 30 minutes.
- If the manifest reports a fallback cut, inspect that join and move it to a nearby sentence boundary if needed.

Never concatenate overlapping transcripts blindly. Retain only segments whose midpoint lies inside the chunk's core interval; the bundled transcription and diarization scripts already enforce this.

## Transcribe text with MLX Whisper

Run:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/transcribe_chunks.py" \
  "outputs/<media-stem>-transcript/prepared/manifest.json" \
  --output-dir "outputs/<media-stem>-transcript/whisper" \
  --language auto
```

Use `--prompt` only for spelling vocabulary, episode topics, and verified proper nouns. Do not use it to force uncertain content. The default model is the locally validated MLX Whisper large-v3-turbo fp16 model.

Inspect these failure signals before editing:

- incomplete coverage or a last timestamp far before the final spoken content;
- repeated phrases, backward timestamps, or timestamps beyond media duration;
- music/outro hallucinations;
- unusually low log probability, high compression ratio, or abrupt language changes.

Set an explicit spoken-content cutoff only after inspecting the audio around the end.

`transcript.raw.json` retains the auditable timestamp data. `transcript.raw.md` contains spoken content only, with no timecodes, so it can be used directly for text analysis. If timestamped subtitles are needed for video editing, use the sibling `audio-to-video-maker` skill to create SRT.

## Add speakers only when useful

Whisper provides the preferred text but no speakers. For interviews, panels, or podcasts where speaker turns matter, run chunked MOSS:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/diarize_chunks.py" \
  "outputs/<media-stem>-transcript/prepared/manifest.json" \
  --output-dir "outputs/<media-stem>-transcript/moss" \
  --vocabulary "verified names and terms for spelling only"

"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/assign_speakers.py" \
  "outputs/<media-stem>-transcript/whisper/transcript.raw.json" \
  "outputs/<media-stem>-transcript/moss/diarization.raw.json" \
  --output "outputs/<media-stem>-transcript/transcript.speakers.json"
```

Treat all generated labels as anonymous. MOSS may hallucinate real-looking names or change a speaker's label across chunks. The script discards generated identities, namespaces labels per chunk, and only links adjacent labels when the overlapping audio supports it.

Map `SPK01` to a real name only when identity is verified by strong evidence such as an explicit self-introduction plus episode metadata and voice continuity. Otherwise retain `SPK01`, `主持人A`, or `嘉宾B`. Mark crosstalk as `[多人重叠]`. Do not run Sortformer v1 offline on a full long recording; the validated 80-minute test exceeded Metal's buffer limit.

Skip diarization when it makes the transcript less reliable, for example narration, very poor audio, or frequent overlap with no stable labels.

## Calibrate, then translate

Read [references/calibration-protocol.md](references/calibration-protocol.md) before editing transcript text. Use [references/reviewed-schema-example.json](references/reviewed-schema-example.json) as the output shape.

Work in logical 10–20 minute batches while carrying the previous two or three calibrated paragraphs as context. Preserve the global timestamps and stable segment alignment.

- Chinese source: correct recognition errors directly into `calibrated`; do not translate unless requested.
- English source: correct English into `calibrated` first, then translate that corrected English into `zh`.
- Code-switching: preserve the original-language phrase when it carries meaning; explain it naturally in the Chinese translation.
- Unclear speech: write `[听不清]`, `[专名待核]`, or `[数字待核]` and add an `unresolved` record. Never guess silently.
- Speaker uncertainty: retain anonymous labels and record uncertainty rather than inventing a mapping.

When names, institutions, dates, or figures materially affect meaning, verify them against show notes or primary sources. Keep raw machine output unchanged for audit.

## Render Markdown and validate

After producing `transcript.reviewed.json`, run:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/render_markdown.py" \
  "outputs/<media-stem>-transcript/transcript.reviewed.json" \
  --output-dir "outputs/<media-stem>-transcript/final" \
  --stem transcript
```

For English, deliver `transcript.en.md`, `transcript.zh.md`, `transcript.bilingual.md`, and `transcript.review.md`. For Chinese, deliver `transcript.zh.md` and `transcript.review.md`. Always retain `transcript.raw.json` and `transcript.raw.md` beside the final files.

Keep every Markdown output free of timecodes. Preserve timing in the raw/reviewed JSON for audit and in SRT when a video-editing handoff is requested.

Before handoff, verify:

1. The final JSON or SRT timestamp reaches the last spoken content and never exceeds media duration.
2. Segment timestamps increase and every end is later than its start.
3. English and Chinese segments remain one-to-one aligned.
4. Every named speaker mapping has an evidence note; anonymous labels remain anonymous otherwise.
5. Proper nouns, acronyms, quotations, dates, currencies, percentages, and other numbers received focused review.
6. Every uncertain item appears in `transcript.review.md`.
7. Report model, runtime, peak memory, chunk boundaries, fallbacks, and known limitations; do not call model disagreement a measured WER without a human reference transcript.
