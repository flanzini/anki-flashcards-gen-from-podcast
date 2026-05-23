# Agent Guidance

This repository turns Ukrainian Lessons Podcast audio or transcripts into
reviewed Anki study material. Keep changes compatible with the local-first
workflow and preserve expensive intermediate work.

## Workflow Rules

- Reuse cached audio and transcripts when available. Do not retranscribe an
  episode unless explicitly requested or the cached transcript is unusable.
- Treat extraction checkpoints as intermediate output only. Cards should be
  reviewed before they are presented as ready for Anki import.
- Prefer resumable work over restarting expensive jobs. Preserve checkpoints,
  logs, extracted snapshots, and reviewed checkpoints after failures.
- When changing output formats, provide a no-model reformat path so completed
  extraction or review work does not need to be repeated.

## Local Model Safety

- Ollama generations can be slow or return malformed JSON. Use bounded output
  length, malformed-JSON retries, and batch-level checkpoints.
- For long jobs, use smaller batches and verify that an early checkpoint is
  written before assuming a mitigation worked.
- If a pipeline run fails, identify whether transcription, extraction, or
  review failed and resume from the most advanced valid artifact.
- Do not delete or overwrite useful partial results while investigating a
  failure.

## Card Design Decisions

- Vocabulary and sentence practice are different study modes and should be
  exported separately for Anki.
- Word-card exports should contain only the Ukrainian term and its English
  meaning so they can be imported as `Basic (and reversed card)` without
  leaking the answer in the reverse direction.
- Sentence-card exports should be forward-only fill-the-gap prompts imported as
  `Basic`.
- Create a sentence card only when the target term can be hidden
  unambiguously in the example sentence. If the example contains only an
  inflected or otherwise unmatched form, keep the vocabulary card and omit the
  sentence card.
- Do not recommend importing mixed word/sentence output using a reversed Anki
  note type.

## Quality And Deduplication

- Remove incorrect, low-value, promotional, garbled, or malformed cards during
  review before import.
- Normalize learner-facing vocabulary where practical, especially when an
  extracted item is merely an unsuitable inflected form.
- Future deduplication should consider both exact matches and close variants
  from earlier sessions or existing Anki notes.

## Files And Organization

- Keep `README.md` focused on human setup, commands, imports, and
  troubleshooting.
- Keep `TODO.md` focused on completed work and future roadmap items.
- Logs, cached transcripts, audio, checkpoints, and generated exports may be
  useful evidence during troubleshooting; do not remove them casually.
- Do not reorganize or move files that a running background pipeline may still
  be writing.
- A future repository cleanup may move generated CSVs into `outputs/` and
  exploratory notebooks or obsolete artifacts into `legacy/`.

## Verification Checklist

- After script edits, run `python -m py_compile episode_to_anki.py`.
- When changing export behavior, test using an existing CSV without a model
  call and inspect representative word and sentence rows.
- For resumed long-running runs, confirm from the log that completed batches
  were skipped and a new checkpoint is written.
- Before telling the user output is ready to import, confirm review has
  completed and identify the correct Anki note type for each generated file.
