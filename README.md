# Podcast Whisper MLX

Local and URL-based audio and video transcription for Apple Silicon, with validated media downloads, silence-aware chunking, MLX-accelerated speech recognition, conservative speaker diarization, transcript calibration, and bilingual Markdown output.

The repository grew out of two practical requirements:

1. Preserve useful timestamps and keep all source media local.
2. Produce reviewable transcripts without pretending that generated speaker names or model-to-model disagreement are ground truth.

The default production path uses MLX Whisper for text. MOSS-Transcribe-Diarize is available as an optional second pass for anonymous speaker turns. Long recordings are split near silence boundaries with overlapping audio context, then merged using non-overlapping core intervals.

## Supported outputs

- Raw timestamped JSON and Markdown from MLX Whisper
- Best-effort anonymous speaker labels from chunked MOSS
- Speaker labels assigned back to the preferred Whisper text
- Calibrated Chinese Markdown for Chinese sources
- Calibrated English, Chinese translation, and bilingual Markdown for English sources
- A separate review list for uncertain names, numbers, quotations, and inaudible passages

Direct HTTP(S) media URLs can be downloaded into a run directory. Video files are accepted directly, and the preparation script extracts the first audio stream before transcription.

## Tested environment

The current repository snapshot was tested on an Apple M4 Pro Mac with 64 GB unified memory.

| Component | Tested version |
|---|---:|
| Python | 3.12.11 |
| uv | 0.11.7 |
| FFmpeg | 8.1 |
| mlx-audio | 0.4.6 |
| mlx | 0.32.0 |
| mlx-lm | 0.31.3 |
| mlx-metal | 0.32.0 |
| transformers | 5.12.1 |
| huggingface-hub | 1.27.0 |
| rapidfuzz | 3.14.5 |

These are tested local versions, not a promise that every future package combination will behave identically. Re-run the smoke tests after changing MLX-Audio, model repositories, or Transformers.

## Installation

### 1. Install system tools

```bash
brew install ffmpeg uv
```

Verify them before creating the Python environment:

```bash
ffmpeg -version
ffprobe -version
uv --version
```

### 2. Create an isolated Python 3.12 environment

Do not use the system Python for this project. The initial machine had a newer system Python than the tested MLX stack, so the project uses a local Python 3.12 environment.

```bash
uv python install 3.12
uv venv --python 3.12 .venv
```

The uv-created environment may not contain the `pip` module. Use `uv pip` rather than `.venv/bin/python -m pip`.

### 3. Install the tested package snapshot

```bash
uv pip install --python .venv/bin/python \
  "mlx-audio==0.4.6" \
  "mlx==0.32.0" \
  "mlx-lm==0.31.3" \
  "mlx-metal==0.32.0" \
  "transformers==5.12.1" \
  "huggingface-hub==1.27.0" \
  "rapidfuzz==3.14.5"
```

Verify the runtime:

```bash
.venv/bin/python --version
uv pip list --python .venv/bin/python | rg '^(mlx|mlx-audio|mlx-lm|transformers|huggingface-hub|rapidfuzz)\s'
```

### 4. Model cache behavior

The bundled project scripts set `HF_HOME` to the repository-local `.cache/huggingface` directory before importing MLX-Audio. This avoids duplicate downloads into a user-level cache and makes the runtime easier to inspect.

The first transcription still needs network access to download models. Later runs can reuse the local cache. Do not commit model files.

## Codex project skill

The project includes a repository-scoped Codex skill at:

```text
.agents/skills/transcribe-media/
```

When Codex is started from this repository, invoke it with a request such as:

```text
$transcribe-media Transcribe and calibrate /absolute/path/to/interview.mp4.
$transcribe-media Download and transcribe https://example.com/episode.mp3.
```

The skill contains the workflow, scripts, calibration rules, speaker identity policy, and reviewed JSON example.

## Basic workflow

Run all commands from the repository root.

```bash
TRANSCRIBE_SKILL_DIR="$PWD/.agents/skills/transcribe-media"
TRANSCRIBE_PYTHON="$PWD/.venv/bin/python"
TRANSCRIBE_RUN_DIR="outputs/example-transcript"
```

Use a new run directory for every source file. The scripts refuse to replace final artifacts unless `--overwrite` is passed explicitly.

### Step 1: Resolve the input

For a direct HTTP(S) audio or video URL, download it first:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/download_media.py" \
  "https://example.com/episode.mp3" \
  --output-dir "$TRANSCRIBE_RUN_DIR/source"
```

The command writes the original media plus `source/download.json`, including the local path, byte count, content type, and SHA-256 hash. It validates each redirect, rejects private/local hosts by default, limits downloads to 4 GiB, refuses overwrite, and strips query parameters from recorded URLs. A webpage URL that does not return media bytes requires a suitable site-specific downloader to resolve it to media first.

For local media, start with its absolute path.

### Step 2: Extract and prepare audio

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/prepare_media.py" \
  "/absolute/path/to/input-audio-or-video" \
  --output-dir "$TRANSCRIBE_RUN_DIR/prepared"
```

The script:

- detects audio and video with `ffprobe`;
- extracts the first audio stream;
- converts it to 16 kHz mono PCM;
- detects longer quiet regions with FFmpeg `silencedetect`;
- targets 25-minute core chunks with a 30-minute maximum;
- adds eight seconds of decoded overlap around each internal boundary;
- records hashes, boundaries, fallbacks, and source metadata in `manifest.json`.

If no suitable silence exists in the allowed window, the script makes a bounded fallback cut and records a warning. Inspect that join before publication.

### Step 3: Transcribe the preferred text with MLX Whisper

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/transcribe_chunks.py" \
  "$TRANSCRIBE_RUN_DIR/prepared/manifest.json" \
  --output-dir "$TRANSCRIBE_RUN_DIR/whisper" \
  --language auto \
  --prompt "Verified names and technical vocabulary only"
```

The default model is `mlx-community/whisper-large-v3-turbo-asr-fp16`. Overlap audio gives the model sentence context, while the merge keeps only segments whose midpoint belongs to each chunk's core interval.

The main outputs are:

```text
whisper/transcript.raw.json
whisper/transcript.raw.md
whisper/chunks/chunk-*.json
```

Treat prompt terms as spelling hints. Do not seed uncertain names or guessed facts.

### Step 4: Add anonymous speaker labels when needed

Skip this step for narration or when speaker separation would add more uncertainty than value.

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/diarize_chunks.py" \
  "$TRANSCRIBE_RUN_DIR/prepared/manifest.json" \
  --output-dir "$TRANSCRIBE_RUN_DIR/moss" \
  --vocabulary "Verified names and terms for spelling only"
```

Then assign the anonymous MOSS turns to the preferred Whisper segments:

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/assign_speakers.py" \
  "$TRANSCRIBE_RUN_DIR/whisper/transcript.raw.json" \
  "$TRANSCRIBE_RUN_DIR/moss/diarization.raw.json" \
  --output "$TRANSCRIBE_RUN_DIR/transcript.speakers.json"
```

The diarization script discards generated names, assigns chunk-local anonymous labels, and reconciles adjacent chunks only when their shared audio supports the mapping. Labels such as `SPK01` are not real identities.

### Step 5: Calibrate and translate

Use the calibration rules in:

```text
.agents/skills/transcribe-media/references/calibration-protocol.md
```

Create `transcript.reviewed.json` using the schema demonstrated by:

```text
.agents/skills/transcribe-media/references/reviewed-schema-example.json
```

Editorial order matters:

- For Chinese sources, correct the Chinese transcript directly.
- For English sources, correct the English transcript first, then translate the corrected English into Chinese.
- Preserve timestamp and segment alignment between English and Chinese.
- Use `[inaudible]`, `[name to verify]`, or `[number to verify]` rather than silently guessing.
- Keep generated speaker labels anonymous unless a name is supported by explicit identification, authoritative episode metadata, and consistent voice evidence.

The raw machine transcript must remain unchanged as an audit artifact.

### Step 6: Render final Markdown

```bash
"$TRANSCRIBE_PYTHON" "$TRANSCRIBE_SKILL_DIR/scripts/render_markdown.py" \
  "$TRANSCRIBE_RUN_DIR/transcript.reviewed.json" \
  --output-dir "$TRANSCRIBE_RUN_DIR/final" \
  --stem transcript
```

English sources produce:

```text
final/transcript.en.md
final/transcript.zh.md
final/transcript.bilingual.md
final/transcript.review.md
```

Chinese sources produce:

```text
final/transcript.zh.md
final/transcript.review.md
```

## Long-recording design

Long audio is deliberately handled as overlapping context plus non-overlapping ownership:

```text
decoded chunk 1:  [---------- audio context ----------]
core chunk 1:     [-------- publishable region -----)
decoded chunk 2:                         [---------- audio context ----------]
core chunk 2:                              [-------- publishable region -----)
```

The shared audio helps both chunks hear a complete boundary sentence. Midpoint ownership prevents the same utterance from being published twice. Silence-centered cuts are preferred, but every fallback cut remains visible in the manifest for spot review.

For dense panels with few pauses, reduce the target to 20–25 minutes. Do not use single-pass MOSS for very long recordings merely because a model card advertises a long maximum duration.

## Installation and implementation lessons

### Keep the environment project-local

Python, model caches, intermediate audio, and user media should remain outside version control. The local `.venv` and `.cache` directories are reproducible and ignored.

An early test accidentally used the user-level Hugging Face cache and began a duplicate model download. The scripts now set the project cache before importing MLX-Audio.

### Prefer Whisper for the primary text

On the tested 80-minute English podcast, MLX Whisper completed the recording in 150.18 seconds, approximately 32.11 times real time, with 4.99 GB peak MLX memory. It covered the full spoken program, although outro music triggered repeated hallucinated text that required an inspected cutoff.

### Chunk MOSS and distrust generated identities

The same 80-minute podcast took 887.17 seconds in a single MOSS pass but silently stopped at 36:19, covering only 45.19 percent. Two approximately 40-minute chunks covered the spoken program, but speaker names drifted and included unsupported identities.

The project therefore uses shorter chunks, anonymous local labels, overlap-based reconciliation, and human identity review. A generated real-looking name is never evidence.

### Do not run Sortformer v1 offline on a full long recording

The tested 80-minute Sortformer v1 offline run attempted to allocate approximately 232.3 GB in one Metal buffer, above the machine's approximately 41.7 GB buffer limit, and failed before useful inference. A future streaming path must be tested independently before it is documented as supported.

### Treat timestamp integrity as a separate quality dimension

Check for negative durations, backward timestamps, timestamps beyond the source duration, missing final coverage, repeated loops, and music-triggered hallucinations. A transcript can look linguistically fluent while having an unusable time axis.

### Do not report model disagreement as WER or DER

The English MOSS-versus-Whisper word disagreement was 3.62 percent, but no human reference transcript was available. The Chinese model-to-model character disagreement was 7.2 percent. These are diagnostic disagreement rates, not measured WER, CER, or DER.

True error rates require a human reference transcript and, for diarization, frame-level or turn-level reference speaker labels.

## Local benchmark summary

| Source | Model/path | Result |
|---|---|---|
| 38:24 Chinese two-host podcast | MOSS hotword run | 300.86 seconds, 7.66x real time, full transcript; one host was briefly split into a third anonymous label |
| 38:24 Chinese two-host podcast | Whisper baseline | 84.85 seconds, 27.15x real time; no speakers and one invalid timestamp segment |
| 80:22 English panel podcast | Whisper baseline | 150.18 seconds, 32.11x real time, 4.99 GB peak MLX memory, full spoken coverage |
| 80:22 English panel podcast | MOSS single pass | 887.17 seconds, stopped at 36:19, 45.19% coverage |
| 80:22 English panel podcast | MOSS in two chunks | 809.24 seconds total, full spoken coverage, unreliable cross-chunk generated names |
| 80:22 English panel podcast | Sortformer v1 full offline | Failed before inference because of a 232.3 GB Metal buffer request |

Detailed machine-generated benchmark artifacts are kept under `outputs/`. They are evidence from these recordings, not universal model benchmarks.

## Validation

Compile the Python scripts:

```bash
.venv/bin/python -m py_compile \
  .agents/skills/transcribe-media/scripts/*.py \
  scripts/*.py
```

Validate the project skill:

```bash
uv run --with pyyaml python \
  /Users/guolin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  .agents/skills/transcribe-media
```

Validate a prepared run before handoff:

```bash
jq -e '.chunks | length > 0' "$TRANSCRIBE_RUN_DIR/prepared/manifest.json"
jq -e '.segments | length > 0' "$TRANSCRIBE_RUN_DIR/whisper/transcript.raw.json"
jq -e 'all(.segments[]; .end > .start)' "$TRANSCRIBE_RUN_DIR/whisper/transcript.raw.json"
```

Do not rerun long or expensive models merely to validate a documentation-only change.

## Repository layout

```text
.
├── .agents/skills/transcribe-media/  # Project-scoped Codex workflow
├── scripts/                          # Benchmark and review helpers
├── outputs/                          # Checked-in benchmark evidence and review artifacts
├── AGENTS.md                         # Codex project instructions
└── README.md                         # Installation and workflow documentation
```

## Data and publication safety

- Keep source audio and video local unless the user explicitly authorizes an upload.
- Do not commit credentials, Hugging Face tokens, model caches, virtual environments, or regenerated WAV files.
- Do not publish raw machine transcripts without human review.
- Verify names, organizations, quotations, dates, percentages, currencies, and other consequential numbers.
- Preserve uncertainty instead of rewriting it as fact.

## References

- [Codex project instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md#layer-project-instructions)
- [MLX-Audio](https://github.com/Blaizzy/mlx-audio)
- [MOSS-Transcribe-Diarize](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)
- [MLX-Audio Sortformer documentation](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/vad/models/sortformer/README.md)
- [pyannote.audio](https://github.com/pyannote/pyannote-audio)
