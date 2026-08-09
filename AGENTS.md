# AGENTS.md

## Project overview

This repository is a local Apple Silicon transcription toolkit for podcasts, interviews, and other long-form audio or video. It uses MLX Whisper for the preferred text, optional chunked MOSS for anonymous speaker turns, FFmpeg silence detection for long-recording boundaries, and a repository-scoped Codex skill for calibration, translation, and Markdown delivery.

The project prioritizes:

- local processing and source-media privacy;
- valid global timestamps and complete spoken-content coverage;
- calibrated Chinese transcripts and calibrated English followed by Chinese translation;
- anonymous speaker labels unless a real identity is supported by evidence;
- raw artifacts that remain available for audit.

## Documentation language

- Write all project documentation in English.
- This rule applies to README files, AGENTS files, architecture notes, workflow instructions, validation notes, and new explanatory Markdown committed to the repository.
- Keep code comments and user-facing CLI help in English.
- Source transcripts and requested translations are content artifacts, not project documentation. Preserve their required source or target language.
- Do not translate existing transcript evidence merely to satisfy the documentation rule.

## Local workflow

- Work from the repository root.
- Use `.venv/bin/python`; do not use the system Python for MLX tasks.
- Use `uv pip` to inspect or change dependencies because the uv-created environment may not include the `pip` module.
- Keep Hugging Face downloads in `.cache/huggingface`. The bundled MLX scripts set this before importing MLX-Audio.
- Use the project skill at `.agents/skills/transcribe-media/` for new audio or video transcription work.
- Create a new `outputs/<media-stem>-transcript/` directory for each run. Do not overwrite unrelated prior artifacts.

## Transcription rules

- Prefer MLX Whisper large-v3-turbo fp16 for the primary transcript.
- Normalize media to 16 kHz mono PCM before model inference.
- For long recordings, use silence-aware chunks with overlap. Keep only the non-overlapping core interval from each chunk.
- Treat every MOSS or diarization speaker label as anonymous until identity is independently verified.
- Use prompt vocabulary only for verified spelling hints. Never seed unverified facts or force a speaker name.
- Inspect the beginning, every fallback join, the final spoken content, and any timestamp anomaly.
- Preserve raw model output. Write calibration and translation into a separate reviewed artifact.
- For English sources, correct the English before translating it into Chinese.
- Mark inaudible passages, uncertain names, and uncertain numbers explicitly instead of guessing.

## Evidence and reporting

- Separate locally measured results from documentation claims, reported benchmarks, and inference.
- Do not call model disagreement a measured WER, CER, or DER without a human reference transcript.
- Report text coverage, timestamp integrity, speaker continuity, and identity confidence separately.
- Do not infer local performance from a model card's maximum duration or feature list.
- Keep exact model versions, runtime versions, input duration, inference time, peak MLX memory, and known failures when adding benchmark evidence.

## Validation

For Python changes, run:

```bash
.venv/bin/python -m py_compile \
  .agents/skills/transcribe-media/scripts/*.py \
  scripts/*.py
```

For skill changes, run:

```bash
uv run --with pyyaml python \
  /Users/guolin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  .agents/skills/transcribe-media
```

Use focused JSON, timestamp, and Markdown checks for changed output logic. Do not rerun long model benchmarks for documentation-only changes.

## Repository hygiene

- Do not commit `.venv`, `.cache`, model weights, credentials, tokens, user media, regenerated WAV files, or Python bytecode.
- Preserve unrelated working-tree changes.
- Stage explicit paths only.
- Keep generated benchmark evidence only when it is intentional, reviewable, and free of private source media.
- Before committing, inspect the staged diff and confirm documentation is in English.
