# Calibration and translation protocol

## Execution boundary

Calibration and translation are performed directly by the active agent. Do not download, install, invoke, or call a separate language model or translation model for this work, whether local or remote. Do not create ad hoc model wrappers to process batches. If the transcript exceeds one context window, process it in smaller editorial batches and validate continuity at every join.

When a publisher or platform transcript is available, preserve it unchanged and treat it as higher-priority text evidence than ASR after verifying that it belongs to the exact episode and covers the delivered media. Timestamped publisher captions should remain the primary time axis unless spot checks reveal material drift or mismatch.

## Evidence order

Use evidence in this order:

1. The audio around the timestamp, including several seconds before and after.
2. A verified publisher transcript or publisher-provided captions for the exact episode.
3. Repeated usage elsewhere in the same recording.
4. Episode title, show notes, on-screen text, or user-provided vocabulary.
5. Primary sources for consequential names, dates, quotations, and figures.
6. Contextual inference, explicitly marked as inference.

Do not replace an uncertain machine reading merely because another plausible phrase sounds smoother.

## Chunk workflow

Edit in logical batches of roughly 10–20 minutes. Include the preceding two or three calibrated paragraphs and the next raw paragraph when processing a boundary. Audio chunks already contain overlap; do not publish duplicate overlap text.

At every join:

- confirm the last complete sentence before the boundary;
- confirm the first complete sentence after the boundary;
- merge a sentence split across two ASR segments without changing its earliest start time;
- remove a duplicated overlap only after confirming both copies represent the same utterance;
- record a fallback time cut as an item requiring spot review.

## Chinese calibration

- Repair homophones, word breaks, punctuation, and obvious grammar introduced by ASR.
- Preserve the speaker's register, repetitions that carry meaning, hedges, jokes, and uncertainty.
- Remove only empty fillers that do not affect meaning; do not make speech sound more certain or polished than it was.
- Standardize a proper noun only after confirming it. Otherwise use `[专名待核]`.
- Preserve units and distinguish percentages from percentage points.

## English calibration

- Correct the English source before translating it.
- Preserve contractions, technical terms, qualifications, and the speaker's level of certainty.
- Resolve names, acronyms, numbers, and domain terms from evidence; do not normalize an acronym into a guessed expansion.
- Prefer readable paragraphs while retaining timestamp and speaker alignment.

## Chinese translation of English

- Translate the calibrated English, never the raw ASR text.
- Preserve claims, modality, negation, dates, units, and numerical precision.
- Keep names and technical terms in English on first occurrence when that helps review, for example `个人消费支出价格指数（PCE）`.
- Translate idioms by meaning rather than word-for-word, but do not add background or conclusions absent from the source.
- Keep segment alignment one-to-one so the English line can audit the Chinese line.

## Speaker policy

Use one of these confidence levels in `speakers`:

- `verified`: explicit self-identification or authoritative episode metadata plus consistent voice evidence.
- `inferred`: strong contextual evidence but not independently confirmed; display a role or anonymous label, not a definite real name.
- `unknown`: no safe mapping.

Generated speaker names are never evidence. For chunked diarization, overlap matching may support label continuity but not human identity. Mark simultaneous voices as `[多人重叠]` instead of choosing one.

## Reviewed JSON contract

Create a JSON object with:

- `metadata.title`, `metadata.source_file`, and `metadata.source_language` (`en` or `zh`);
- `speakers[]` with `id`, `display_name`, `confidence`, and `evidence`;
- `segments[]` with increasing `start`, `end`, `speaker`, original machine `source`, corrected `calibrated`, and optional `uncertain`;
- English sources additionally require `zh` for every segment;
- `unresolved[]` with `start`, `type`, and `note`.

Keep the raw transcript as a separate immutable artifact. Do not overwrite it with the reviewed JSON.

## Quality boundary

Without a human reference transcript, report sampled corrections, model disagreement, or an explicitly labeled operational estimate. Do not report any of them as true WER or DER. State separately whether text coverage, timestamp integrity, speaker continuity, and identity mapping passed.
