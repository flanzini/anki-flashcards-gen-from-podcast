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
- For commute listening without cloud hosting, prefer weekend batch prep:
  `--transcribe-only` for several episodes, then extract/review separately before
  the listening week. Sync `transcripts/` (and optional `audio/`) to the phone
  via OneDrive or similar; push approved cards to Anki before listening.

## Local Model Safety

- Ollama generations can be slow or return malformed JSON. Use bounded output
  length, malformed-JSON retries, and batch-level checkpoints.
- For long jobs, use smaller batches and verify that an early checkpoint is
  written before assuming a mitigation worked.
- On the current 8 GB Acer Swift development machine, `qwen3:8b` is suitable
  only for small experiments: a six-card review took about 402 seconds and
  reduced free RAM to about 0.4 GB. Do not use it for routine full-episode
  review without explicit user agreement.
- For routine local runs on this machine, default to `qwen3:4b` and conservative
  batch sizes (for example `--batch-chars 1200 --cards-per-batch 6
  --review-batch-size 4`).
- When the user has OpenAI API credits, prefer `--vocab-provider openai` and
  `--review-provider openai` with `gpt-4o-mini`: combined single-pass review,
  default review batch size 60, and Anki `--duplicate-policy skip` when
  `--crosscheck-anki` is set without an explicit policy. Do not assume a ChatGPT
  subscription includes API credits.
- If possible, schedule full-episode review runs after finishing other
  memory-heavy work. Treat this as the preferred reliability setting rather
  than trying to force hard memory caps.
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
- Create a sentence card only when `ExampleTarget` records the exact surface
  form that can be hidden unambiguously in the example sentence. A lemma such
  as `йогурт` may use `йогуртом` as its example target; never blank only part
  of an inflected word.
- If the example target is missing or leaves too little context, keep the
  vocabulary card and omit the sentence card.
- Do not recommend importing mixed word/sentence output using a reversed Anki
  note type.

## Quality And Deduplication

- Remove incorrect, low-value, promotional, garbled, or malformed cards during
  review before import.
- Normalize learner-facing vocabulary where practical, especially when an
  extracted item is merely an unsuitable inflected form.
- Treat automated review as advisory rather than import approval; inspect both
  retained and rejected cards before importing an episode.
- Keep model responsibilities separated: usefulness triage should not edit
  lexical content; lexical cleanup should not reject useful cards solely due
  to weak examples; sentence-card safety should remain deterministic.
- Keep useful standalone terms as vocabulary cards even when the only example
  is the term itself; omit the sentence card rather than dropping the term.
- Preserve plausible uncertain cards in the `_needs_review.csv` audit. Only
  place clear noise or malformed material in `_rejected.csv`.
- A separated-pass `qwen3:4b` probe preserved useful short-example vocabulary
  but still accepted an unchanged mistranslation with no pending flag. Do not
  assume the needs-review queue catches all lexical errors.
- Preserve and inspect the `_rejected.csv` audit produced by review runs; a
  dropped card may reveal a false negative even when the retained output looks
  clean.
- For final human validation, use `card_review_web.py` to merge accepted,
  pending, and rejected queues and export only explicitly accepted cards. The
  current Conda environment cannot create Tkinter windows because Tcl/Tk is
  unavailable.
- Push cards to Anki only from approved exports or the final validation UI,
  using `anki_connect.py`/AnkiConnect. Keep word cards on `Basic (and reversed
  card)` and sentence cards on `Basic`.
- Remember that Anki displays deck names containing `::` as expandable parent
  and child decks; explain that behavior when selecting destination names.
- Preferred Anki hierarchy for this learner setup:
  `Ukrainian::Chapter 1 Book` (manual textbook cards), `Ukrainian::Podcast`,
  and episode decks such as `Ukrainian::ULP 4-134`. For `--crosscheck-anki`,
  default examples should use parent deck `Ukrainian` so nested decks are
  included. Do not recommend the obsolete top-level names
  `Chapter #1 Ukrainian Book` or `Ukrainian Podcast`, or the non-existent
  `Ukrainian::ULP` path (matches zero notes).
- Treat managed AnkiConnect tags as the safe update key. Do not claim that
  earlier manually imported notes will be automatically updated or deduplicated.
- ULP 2-50 provides regression examples of semantic target mismatch and false
  negative rejection. Use these examples to test general quality rules; do not
  encode episode-specific vocabulary corrections into production logic.
- Future deduplication should consider both exact matches and close variants
  from earlier sessions or existing Anki notes.

## Cloud Transcript Delivery

- `cloud/transcript_service/` is an optional phone-triggered transcript delivery
  service (Cloud Run + Speech-to-Text + GCS + email). It is separate from the
  local Anki extraction/review pipeline and **not required** for routine study.
- **Default without GCP credits:** use local `--transcribe-only` batch prep and
  phone sync of cached `transcripts/`. Treat GCP deployment as on hold until
  credits or a self-hosted alternative (home tunnel + faster-whisper) is chosen.
- The service supports local dry runs (`TRANSCRIPT_SPEECH_BACKEND=mock`,
  `LOCAL_DATA_DIR`) without billing; production GCP steps live in
  `cloud/deploy.md`.
- Reuse RSS episode selection conventions from `podcast_to_anki.py`. Prefer cache
  hits over re-transcription whether storage is GCS or local.
- Never commit API tokens, SMTP passwords, or service-account keys. Document
  secrets via environment variables / Secret Manager only.
- Do not assume cloud transcripts have been human-reviewed for Anki import.

## Files And Organization

- Keep `README.md` focused on human setup, commands, imports, and
  troubleshooting.
- Keep `TODO.md` focused on completed work and future roadmap items.
- Logs, cached transcripts, audio, checkpoints, and generated exports may be
  useful evidence during troubleshooting; do not remove them casually.
- Do not reorganize or move files that a running background pipeline may still
  be writing.
- Store generated CSVs under `outputs/<episode>/`; keep checkpoints, review
  audits, and manual decisions there, with import-ready exports in
  `outputs/<episode>/approved/`.
- Use `outputs/misc/` for scratch or historical generated CSVs that do not
  belong to an episode. Keep committed example input under `examples/`.
- Exploratory notebooks or obsolete source artifacts may later move into
  `legacy/`; do not mix that archival work with generated output cleanup.

## Documentation Sync

- When modifying `episode_to_anki.py`, check whether the change affects CLI
  options, generated files, review or validation behavior, import instructions,
  or workflow assumptions.
- If documentation or persistent agent guidance is affected, update
  `README.md`, `TODO.md`, and/or `AGENTS.md` as part of the same task.
- If no documentation update is needed, mention that the documentation impact
  was checked in the final summary.

## Verification Checklist

- After script edits, run `python -m py_compile episode_to_anki.py`.
- When changing export behavior, test using an existing CSV without a model
  call and inspect representative word and sentence rows.
- When changing review prompts, review logic, or model choice for an episode
  with a saved fixture, run its `--evaluate-input` quality check and report
  any failed expectations before presenting output as improved.
- For resumed long-running runs, confirm from the log that completed batches
  were skipped and a new checkpoint is written.
- Before telling the user output is ready to import, confirm review has
  completed, inspect validation, needs-review, and rejected-card reports, run
  any applicable quality fixture, complete any required UI validation, and
  identify the correct Anki note type for each generated file.
