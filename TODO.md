# TODO

## Completed

- [x] Checkpoint extracted cards after each completed extraction batch.
- [x] Checkpoint reviewed cards after each completed review batch.
- [x] Resume batched extraction from an existing checkpoint with completed-batch skipping.
- [x] Retry malformed Ollama JSON responses and cap model output length.
- [x] Review an existing CSV and write cleaned Anki-ready output.
- [x] Reformat an existing CSV without calling the model again.
- [x] Export separate study files:
  - bidirectional vocabulary cards for `Basic (and reversed card)`
  - forward-only fill-the-gap sentence cards for `Basic`
- [x] Track exact example surface forms for safe sentence gaps, including inflected forms.
- [x] Generate a validation report for reviewed cards before import.

## Anki Integration

- [x] Add AnkiConnect API support for pushing approved cards into local Anki.
  - Detect whether AnkiConnect is running at `http://127.0.0.1:8765`.
  - Provide `anki_connect.py` for approved split CSVs and a push action in the final browser review UI.
  - Configure the destination deck with `--deck` or `--anki-deck`.
  - Document Anki nested-deck naming with `Parent::Child` and the expandable parent display.
  - Configure separate word/sentence note models and optional updates to managed existing notes.
  - Create the deck if it does not exist.
  - Push reviewed word and sentence cards with their appropriate note models.
  - Update already-pushed managed cards when reviewed output changes instead of adding duplicates.
  - Preserve source, episode, and card type as searchable Anki tags when using built-in note models.
  - Fail gracefully if Anki is not open or AnkiConnect is not installed.
  - Document setup:
    - install Anki desktop
    - install the AnkiConnect add-on
    - keep Anki open while pushing cards
- [ ] Consider matching or migrating cards previously imported manually before managed AnkiConnect tags existed.

## Card Quality

- [x] Improve the review pass so it checkpoints reviewed cards after every batch.
- [ ] Continue tuning the review prompt to be stricter about:
  - dropping outro, website, membership, and social-media cards
  - removing malformed transcript artifacts
  - normalizing inflected forms to dictionary forms
  - keeping grammar cards only when they are central to the episode
- [x] Add deterministic guards against non-Ukrainian examples, invalid sentence targets, and low-context gaps.
- [ ] Add a `--min-quality` or `--strictness` option for different learner preferences.
- [x] Validate that `ExampleTarget` plausibly expresses the card headword, not merely a word present in the example.
  - Use conservative, general rules for safe sentence export; when uncertain, keep only the vocabulary card.
  - Use ULP 2-50 target mismatches as regression examples, not hardcoded production corrections.
- [ ] Improve general normalization and translation-quality review for recurring error classes.
  - Prefer dictionary forms over incidental inflections or quantified phrases when the base term is the learning target.
  - Flag suspect translation/transcription inconsistencies for review rather than hardcoding episode-specific replacements.
- [x] Separate local model review responsibilities into usefulness triage and lexical cleanup passes.
  - Keep sentence-card safety deterministic after model review.
- [x] Retain useful standalone vocabulary as word cards when examples are too short for sentence practice.
- [x] Preserve plausible uncertain cards in a `*_needs_review.csv` queue instead of dropping them silently.
- [x] Add an optional focused recovery pass for cards remaining in the needs-review queue.
- [ ] Add an independent lexical challenge pass or targeted manual-review rule for accepted cards.
  - The split-pass `qwen3:4b` probe preserved useful terms but still confidently accepted an unchanged bad translation.
  - Route suspected translation or lemma uncertainty into `*_needs_review.csv` even when the first lexical pass says accept.
- [x] Add an episode quality-audit fixture using the observed ULP 2-50 failures.
  - Treat its individual words and phrases as observed regression cases for general quality behavior.
- [x] Capture reviewer drop decisions and reasons, and generate a dropped-card audit report before import.
- [x] Add a local visual interface for final accept/correct/reject validation before Anki import.
- [ ] Optional: add a targeted OpenAI API final-adjudication pass after local review.
  - Keep transcription, extraction, deterministic validation, and routine processing local.
  - Send only the final candidate cards plus rejected-card audit for stronger linguistic judgment.
  - Evaluate adjudicated output against saved quality fixtures before import.
  - Keep this optional because API use is billed separately from ChatGPT Pro.
- [x] Add OpenAI combined review path (`--review-provider openai`) with larger batches.
  - Single triage+lexical pass instead of the local multi-pass design.
  - With OpenAI review + `--crosscheck-anki`, default `--duplicate-policy` to `skip`.
  - Document prepaid API credit setup (separate from ChatGPT subscription).

## Deduplication

- [ ] Cross-reference newly generated vocabulary against cards from previous sessions.
  - Load existing exported/reviewed CSVs and, later, cards already stored in Anki.
  - [x] Cross-check generated cards against existing Anki notes before review and mark matches as `duplicate_card` for manual validation.
  - [x] Detect near-duplicates with fuzzy front-text matching and write a duplicate audit report with matched note metadata.
  - [x] Add configurable duplicate policy (`needs_review`, `skip`, `keep`) for cross-check behavior.
  - [x] Reorganize local Anki Ukrainian decks under parent `Ukrainian` via AnkiConnect
    (`Chapter 1 Book`, `Podcast`, episode decks) so `--crosscheck-anki-deck "Ukrainian"`
    covers manual and podcast cards in one query.
  - [ ] Tune duplicate thresholds and matching rules against real backfill runs to reduce false positives.
  - Keep a configurable policy for whether to skip, replace, or enrich an existing card with a better example.
  - Log which new items were excluded and which existing card they matched.

## Workflow

- [x] Add a command that reviews an existing CSV and writes a cleaned Anki-ready CSV.
- [x] Add a no-model command that reformats a generated/reviewed CSV into split Anki import files.
- [x] Add `--transcribe-only` to download/transcribe episodes without vocabulary extraction.
- [ ] Add a short status command or helper that summarizes:
  - whether transcription exists
  - whether extraction completed
  - number of vocabulary cards and sentence cards
  - latest log errors
- [ ] Add `.apkg` export as an alternative to CSV import for cloud/mobile handoff.
  - Prefer deterministic packaging from already approved split CSVs.
  - Keep note-type mapping consistent: words -> `Basic (and reversed card)`, sentences -> `Basic`.

## Repository Organization

- [x] Reorganize generated artifacts into clear dedicated folders.
  - Store generated and reviewed CSV exports under episode folders in `outputs/`.
  - Keep each episode's checkpoints, review audits, and manual decisions together, with import-ready exports in an episode-level `approved/` folder.
  - Keep logs under `logs/` and transcripts/audio under their existing cache folders.
  - Use `outputs/misc/` for historical scratch or non-episode CSV exports.
  - Update default paths, documentation, and `.gitignore` rules after the folder convention is chosen.
- [ ] Create a `legacy/` archive folder for files that are no longer part of the main workflow.
  - Move the exploratory Jupyter notebooks there if they are still worth retaining.
  - Review old sample/test CSVs and example files before moving or removing them.
  - Keep only files used by the current CLI workflow at the repository root.

## Listening Support

- [x] Phone on-demand transcript delivery service (code complete; GCP deploy on hold).
  - Service lives under `cloud/transcript_service/`; deploy notes in `cloud/deploy.md`.
  - Authenticated `POST /v1/transcripts` with RSS episode index or title search.
  - Cache transcripts in GCS or local storage so repeat requests skip Speech-to-Text.
  - **Preferred path without GCP credits:** weekend `--transcribe-only` batch runs +
    OneDrive (or similar) sync of `transcripts/` to the phone.
- [ ] Deploy transcript service to GCP when credits or billing are available again.
- [ ] Self-hosted transcript service (home PC + faster-whisper + tunnel) as a no-GCP
  on-demand alternative to Cloud Run.
- [ ] Local audio + transcript viewer for side-by-side listening on the PC.
- [ ] Timestamped / synced transcript export (SRT/VTT) for highlight-while-playing.
- [ ] Optional in-browser download page (no email) for phone clients.
- [ ] Optional Google Drive sync of local `transcripts/` as a non-cloud fallback.

## Cloud Execution

- [ ] Support running the pipeline remotely instead of only on the local Windows machine.
  - Start with CPU VM + local-model workflow; treat GPU hosting as an optional later optimization.
  - Store transcripts, checkpoints, reviewed cards, and logs in durable cloud storage.
  - Make interrupted jobs resumable across machines/runs.
  - Keep API keys and credentials outside checked-in files.
  - Keep checkpoint/review artifact layout compatible with current `outputs/<episode>/` conventions.
- [x] Estimate cloud runtime costs from observed local logs for planning.
  - Use ~2.8 hours/episode as a baseline full-run estimate until cloud benchmarks are captured.
- [ ] Benchmark one representative episode on a candidate GCP VM and compare with local baseline.
  - Measure transcription, extraction, and review phases separately.
  - Record on-demand and Spot costs with runtime variance.
- [x] Add a remote queue/trigger path that can be started from mobile (transcript-only).
  - Implemented: authenticated Cloud Run webhook plus optional Cloud Tasks worker enqueue.
  - [ ] Option B later: RSS polling trigger for new episodes.
  - [ ] Extend the same trigger pattern to full extract/review jobs when remote Anki processing is ready.
- [ ] Add remote run status visibility suitable for mobile.
  - Expose per-episode stage (`transcribe`, `extract`, `review`, `validate`) and log tail.
- [ ] Add a remote final-validation UX path for mobile browser use.
  - Host the existing review flow behind authentication, or provide an equivalent web review UI.
  - Preserve accepted/pending/rejected outputs and final approved export behavior.
- [ ] Define cloud-to-Anki delivery options explicitly in docs and scripts.
  - Preferred low-friction path: approved split CSV and/or `.apkg` export for later import.
  - Optional advanced path: secure home AnkiConnect bridge for one-tap remote push.
  - Android-specific path to evaluate separately: direct AnkiDroid API integration via a companion app.
