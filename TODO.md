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

## Deduplication

- [ ] Cross-reference newly generated vocabulary against cards from previous sessions.
  - Load existing exported/reviewed CSVs and, later, cards already stored in Anki.
  - Drop exact duplicate lemmas before review/import.
  - Detect near-duplicates such as inflected forms, spelling variants, and synonymous short phrases.
  - Keep a configurable policy for whether to skip, replace, or enrich an existing card with a better example.
  - Log which new items were excluded and which existing card they matched.

## Workflow

- [x] Add a command that reviews an existing CSV and writes a cleaned Anki-ready CSV.
- [x] Add a no-model command that reformats a generated/reviewed CSV into split Anki import files.
- [ ] Add a short status command or helper that summarizes:
  - whether transcription exists
  - whether extraction completed
  - number of vocabulary cards and sentence cards
  - latest log errors
- [ ] Consider exporting `.apkg` files as an alternative to CSV import.

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

## Cloud Execution

- [ ] Support running the pipeline remotely instead of only on the local Windows machine.
  - Decide between a managed API workflow and hosting local-model equivalents on a cloud GPU.
  - Store transcripts, checkpoints, reviewed cards, and logs in durable cloud storage.
  - Make interrupted jobs resumable across machines/runs.
  - Keep API keys and credentials outside checked-in files.
  - Estimate ongoing transcription/model/runtime costs before selecting an implementation.
