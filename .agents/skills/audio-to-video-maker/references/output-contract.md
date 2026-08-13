# Editing handoff contract

Use this reference when checking filenames, schemas, or limitations of an audio-to-video handoff.

## Required delivery

For a Chinese recording named `episode-001.wav`, deliver:

```text
episode-001.wav
episode-001.zh-CN.raw.srt
episode-001.zh-CN.words.json
```

Keep the original audio container when the input is audio. When the input is video, deliver the normalized 16 kHz mono WAV extracted by `prepare_media.py`. In both cases, keep one shared basename.

## SRT contract

- Encode as UTF-8 and use `HH:MM:SS,mmm` timestamps.
- Keep timestamps non-negative, monotonic, non-overlapping, and within the delivered audio.
- Prefer natural speech phrases of 1–8 seconds.
- Preserve fillers, false starts, repetitions, corrections, numbers, units, company names, and English terms present in the recording.
- Use `[听不清]` only after listening confirms that speech cannot be resolved. Never guess.
- Retain meaningful sounds such as `[笑]` or `[咳嗽]` only when audible; represent ordinary silence as a time gap.
- Do not add speaker labels to single-speaker narration, editing advice, B-roll notes, Markdown, summaries, or polished subtitle wording.

## Word JSON contract

```json
{
  "schema_version": "1.0",
  "language": "zh-CN",
  "audio_file": "episode-001.wav",
  "duration_seconds": 31.3,
  "timestamp_source": "mlx-whisper-cross-attention-dtw",
  "segments": [
    {
      "id": 1,
      "start": 0.82,
      "end": 4.26,
      "text": "今天我们来聊一个问题。",
      "words": [
        {"text": "今天", "start": 0.82, "end": 1.18}
      ]
    }
  ]
}
```

Make `segments` one-to-one with SRT cues. Chinese `words` may be words, characters, or ASR subword tokens. Keep each available token's model-derived start and end time; do not invent evenly distributed token times.

`mlx-whisper-cross-attention-dtw` timestamps are alignment estimates, not sample-accurate forced-alignment ground truth. If usable word timestamps are unavailable, keep `words` arrays empty, set `timestamp_source` to `segment-level-only`, and still deliver the raw SRT.

## Markdown separation

When a content-analysis Markdown file is requested, render only the spoken text and content structure. Do not include timecodes. Use the SRT for human-facing video-cut locations and keep JSON timing as machine-readable alignment evidence.

## Semantic review boundary

Automated validation can prove structural properties and compare timestamps with the audio duration. It cannot prove that ASR retained every filler, identify an inaudible phrase, or distinguish real speech from a fluent music-tail hallucination. Listen to the beginning, every chunk join, low-confidence or suspicious passage, and the final spoken-content boundary before delivery.
