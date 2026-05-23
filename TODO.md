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

## Anki Integration

- [ ] Add AnkiConnect API support for creating and updating cards in local Anki.
  - Detect whether AnkiConnect is running at `http://127.0.0.1:8765`.
  - Add CLI flags:
    - `--push-to-anki`
    - `--anki-deck "Ukrainian::ULP 2-49"`
    - `--anki-model "Basic"`
    - `--anki-update-existing`
  - Create the deck if it does not exist.
  - Push reviewed word and sentence cards with their appropriate note models.
  - Update already-pushed cards when reviewed output changes instead of adding duplicates.
  - Preserve tags, source, episode, and card type.
  - Fail gracefully if Anki is not open or AnkiConnect is not installed.
  - Document setup:
    - install Anki desktop
    - install the AnkiConnect add-on
    - keep Anki open while pushing cards

## Card Quality

- [x] Improve the review pass so it checkpoints reviewed cards after every batch.
- [ ] Continue tuning the review prompt to be stricter about:
  - dropping outro, website, membership, and social-media cards
  - removing malformed transcript artifacts
  - normalizing inflected forms to dictionary forms
  - keeping grammar cards only when they are central to the episode
- [ ] Add a `--min-quality` or `--strictness` option for different learner preferences.

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

- [ ] Reorganize generated artifacts into clear dedicated folders.
  - Store generated and reviewed CSV exports under a single output folder, such as `outputs/`.
  - Decide whether checkpoints belong under `outputs/checkpoints/` or a separate transient work folder.
  - Keep logs under `logs/` and transcripts/audio under their existing cache folders.
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
