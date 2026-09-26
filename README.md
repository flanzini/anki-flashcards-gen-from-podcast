# Ukrainian Podcast Transcript to Anki

Goal: turn a podcast episode into Anki cards with Ukrainian vocabulary on the
front and English meanings on the back.

The free local pipeline is:

```text
podcast RSS/audio -> faster-whisper transcript -> Ollama vocabulary extraction -> Anki CSV
```

## One-Time Setup

Install Python dependencies in the `expenses` Conda environment:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses pip install faster-whisper
```

Install Ollama for Windows:

```text
https://ollama.com/download/windows
```

Then open a new PowerShell and download a local model:

```powershell
ollama pull qwen3:4b
```

`qwen3:8b` can produce better translations, but it needs more memory. On this
machine, `qwen3:4b` is the safer default.

## Pipeline Dashboard (recommended UI)

Double-click [`Launch ULP Dashboard.bat`](Launch%20ULP%20Dashboard.bat) (or run
the command below). A browser opens at `http://127.0.0.1:8787/` with three tabs:

1. **Transcribe** — search/filter RSS episodes, run local `faster-whisper` or
   OpenAI Whisper API, and **Stop** queued/running jobs from the Jobs panel.
2. **Generate** — choose Local (`qwen3:4b`) or OpenAI (`gpt-4o-mini`) flashcard
   generation; keep Anki open (AnkiConnect) so duplicates can be checked against
   parent deck `Ukrainian`
3. **Review** — accept / edit / reject cards, export approved CSVs, push to Anki

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python pipeline_dashboard.py
```

Leave the launcher window open while using the dashboard. Stop with `Ctrl+C`.
For OpenAI transcription or generation, put `OPENAI_API_KEY=...` in a local
`.env` file in the repo root (gitignored), then relaunch the dashboard. The
dashboard never writes the API key to disk itself.

## Local MP3 To Anki

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --episode-name "ULP 3-103" --output outputs\ulp_3_103\anki_vocab.csv
```

This uses:

- `faster-whisper` with model `medium` for Ukrainian transcription
- Ollama model `qwen3:4b` for vocabulary extraction and English translation

The transcript is cached in `transcripts/`, so reruns do not retranscribe unless
you add `--force-transcribe`.

## Existing Transcript To Anki

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --transcript-file transcript.txt --episode-name "ULP 3-103" --output outputs\ulp_3_103\anki_vocab.csv
```

## RSS Episode To Anki

Newest episode from the default Ukrainian Lessons Podcast RSS feed:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --episode-index 0 --output outputs\latest\anki_vocab.csv
```

Find by title text:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --title-search "doctor" --output outputs\doctor\anki_vocab.csv
```

Downloaded audio is cached in `audio/`.

## Transcribe Only (Weekend Batch Prep)

Download and transcribe episodes without running vocabulary extraction or review.
Useful when you want several transcripts ready before a later extract/review pass.

Newest episode:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --episode-index 0 --transcribe-only --log-file logs\transcribe_0.log
```

Batch several RSS episodes (newest first):

```powershell
foreach ($i in 0, 1, 2) {
  & "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py `
    --episode-index $i --transcribe-only --log-file "logs\transcribe_$i.log"
}
```

Transcripts land in `transcripts/` and audio in `audio/`. Cached transcripts are
reused on reruns unless you add `--force-transcribe`. Later, run the full pipeline
with `--transcript-file` or the same RSS selection to skip retranscription.

## Useful Options

Use a larger local LLM if you have enough memory:

```powershell
--ollama-model qwen3:8b
```

Use a larger Whisper model for better transcription:

```powershell
--transcription-model large-v3
```

Limit card count:

```powershell
--max-cards 30
```

Give slower local models more time:

```powershell
--ollama-timeout 900
```

Bound long or runaway local-model responses:

```powershell
--ollama-num-predict 1024
```

Retry malformed local-model JSON responses:

```powershell
--ollama-retries 2
```

Run local extraction in smaller batches:

```powershell
--batch-chars 4000 --cards-per-batch 8
```

Low-RAM profile for the 8 GB laptop (slower but safer while avoiding stalls):

```powershell
--ollama-model qwen3:4b --batch-chars 1200 --cards-per-batch 6 --review-batch-size 4
```

If you plan to run a full episode review, prefer starting it when other heavy
apps/work are finished. This reduces timeout risk and keeps the machine
responsive.

Show progress and keep a log file:

```powershell
--log-level INFO --log-file logs\episode_103.log
```

Resume from the transcript we already generated:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --transcript-file transcripts\ULP_3-103.txt --episode-name "ULP 3-103" --output outputs\ulp_3_103\anki_vocab.csv --max-cards 40 --batch-chars 4000 --cards-per-batch 8 --ollama-timeout 900 --log-file logs\episode_103.log
```

The script writes the output CSV after every completed batch, so an interrupted
run still leaves partial cards in the output file.

## Output Layout

Generated CSV files belong under `outputs/`, not in the repository root:

```text
outputs/
  ulp_2_50/
    anki_vocab_ulp_2_50_v4_reviewed_checkpoint.csv
    anki_vocab_ulp_2_50_v4_needs_review.csv
    anki_vocab_ulp_2_50_v4_rejected.csv
    anki_vocab_ulp_2_50_v4_manual_review.csv
    approved/
      anki_vocab_ulp_2_50_v4_approved_words.csv
      anki_vocab_ulp_2_50_v4_approved_sentences.csv
  misc/
```

Keep all generated artifacts for one episode together. Place import-ready files
under the episode's `approved/` folder, separate from checkpoints, audits, and
manual-review saves. Use `outputs/misc/` for scratch or historical generated CSVs that are not attached to one episode.
Committed sample data lives under `examples/`; logs remain in `logs/`.

## Anki Import

The generated CSV columns are:

```text
Ukrainian, English, Example, ExampleTarget, Tags, Source, Episode
```

In Anki, map:

- `Ukrainian` to the front field
- `English` to the back field
- `Example` to an extra field if your note type has one
- `ExampleTarget` stores the exact inflected form used for safe fill-the-gap sentence exports
- `Tags` to Anki tags

Review the CSV before importing. Local models are useful, but a quick human pass
is still worthwhile for language-learning cards.

For direct Anki Basic cards, use:

```powershell
--card-format basic
```

For two Basic cards per vocabulary item, one word card and one example-sentence
card, use:

```powershell
--card-format basic-with-sentences
```

Import `basic` and `basic-with-sentences` output into Anki using the one-direction
`Basic` note type, not `Basic (and reversed card)`. Sentence cards show a
fill-the-gap prompt only when the target term occurs explicitly in the example;
examples using a different inflected form remain on the vocabulary card but do
not create an ambiguous sentence prompt.

That writes:

```text
Front, Back, Tags, Source, Episode, CardType
```

For recommended two-direction vocabulary practice plus forward-only sentence
practice, use:

```powershell
--card-format bidirectional-with-sentences --output outputs\ulp_2_50\anki_vocab_ulp_2_50.csv
```

This writes two CSV files:

```text
outputs\ulp_2_50\anki_vocab_ulp_2_50_words.csv
outputs\ulp_2_50\anki_vocab_ulp_2_50_sentences.csv
```

Import `*_words.csv` using Anki's `Basic (and reversed card)` note type. The
word cards contain only Ukrainian and English, so the English-to-Ukrainian
prompt does not reveal its answer. Import `*_sentences.csv` using the
one-direction `Basic` note type; its fronts are Ukrainian fill-the-gap prompts.
Sentence prompts hide the exact surface form from `ExampleTarget`, so a lemma
such as `йогурт` can correctly produce a gap for `йогуртом` instead of a broken
partial-word prompt. Export also applies a conservative surface-form relation
check between the vocabulary headword and its target. If a target appears in
the example but cannot plausibly be the inflected headword, the vocabulary card
is retained and the sentence card is omitted for manual review.
Likewise, a useful standalone word whose only example is the word itself is
kept in `*_words.csv` and simply does not produce a sentence card.

To export these two files from an already generated or reviewed CSV without
calling the model again:

```powershell
python episode_to_anki.py --format-input outputs\ulp_2_50\reviewed.csv --output outputs\ulp_2_50\anki_vocab_ulp_2_50.csv --card-format bidirectional-with-sentences
```

## Review / Validate Generated Cards

For better quality, add a second local Ollama review pass:

```powershell
--review-cards --review-batch-size 20
```

You can also review an existing generated CSV without retranscribing or
extracting again:

```powershell
C:\Users\filip\Miniconda3\envs\expenses\python.exe episode_to_anki.py --review-input outputs\ulp_2_49\anki_vocab_ulp_2_49.csv --output outputs\ulp_2_49\anki_vocab_ulp_2_49_reviewed.csv --card-format basic-with-sentences --ollama-timeout 900 --log-file logs\episode_2_49_review.log
```

The reviewer uses separate model passes rather than asking one response to do
everything:

- a selection pass retains plausible useful concepts and rejects only clear noise
- a lexical pass normalizes retained vocabulary, translations, and candidate examples
- deterministic export checks decide whether a sentence target is safe to use

A short or one-word example is not by itself a reason to reject a useful term:
the reviewer should retain it as a vocabulary-only card and omit the sentence
exercise.

Use `--recover-needs-review` to run an optional additional lexical pass on
cards still held for manual review. Because the local model is called more
than once per batch, separated review is slower than the earlier one-pass
review but is designed to avoid silently losing useful words.

When `--review-cards` is used during extraction, the unreviewed extracted cards
are also saved to an `_extracted.csv` snapshot before review starts.
Review output also writes a `_validation.csv` report identifying missing or
unsafe example targets and examples unsuitable for sentence cards.
Review output also writes a `_needs_review.csv` audit for plausible cards that
need judgment and a `_rejected.csv` audit for clear rejects. Inspect both
alongside accepted cards before importing.

Example review-only rerun with focused recovery:

```powershell
python episode_to_anki.py --review-input outputs\ulp_2_50\extracted.csv --output outputs\ulp_2_50\reviewed.csv --card-format bidirectional-with-sentences --review-batch-size 6 --recover-needs-review --ollama-timeout 900
```

Optional Anki duplicate cross-check before review:

```powershell
python episode_to_anki.py --review-input outputs\ulp_2_50\extracted.csv --output outputs\ulp_2_50\reviewed.csv --card-format bidirectional-with-sentences --review-batch-size 6 --crosscheck-anki --crosscheck-anki-deck "Ukrainian" --crosscheck-fuzzy-threshold 0.84 --ollama-timeout 900
```

Use the parent deck name `Ukrainian` so AnkiConnect includes nested child decks
(book, podcast, and episode decks). A non-existent path such as `Ukrainian::ULP`
matches zero notes and silently skips deduplication.

When enabled, cards that match existing Anki notes (exact or fuzzy front-text
match) are moved to `*_needs_review.csv` with reason `duplicate_card` and match
metadata (note id, matched front/back, score). A `*_duplicates.csv` audit is
also written so potential repeats can be reviewed explicitly instead of silently
dropped.

Use `--duplicate-policy` to control matched-card handling:

- `needs_review` (default): send matched cards to manual review.
- `skip`: drop matched cards from reviewed output, keep them in `*_duplicates.csv`.
- `keep`: keep matched cards in reviewed output, but still log matches in `*_duplicates.csv`.

Generate a validation report for an existing reviewed CSV without any model
calls:

```powershell
python episode_to_anki.py --validate-input outputs\ulp_2_50\reviewed.csv --validation-output outputs\ulp_2_50\validation.csv
```

Evaluate reviewed cards against a saved episode quality fixture without any
model calls:

```powershell
python episode_to_anki.py --evaluate-input outputs\ulp_2_50\anki_vocab_ulp_2_50_v4_reviewed_checkpoint.csv --quality-fixture quality_fixtures\ulp_2_50.json --quality-output logs\episode_2_50_v4_quality.csv
```

The command exits with a failure code when any expected keep, reject,
translation, or sentence-target check fails, so it can be reused as a
regression check while prompts or model choices change.

## Final Validation Interface

Use the local browser reviewer to make the final accept, correction, or reject
decisions before importing cards into Anki. It runs only on your computer at a
local `http://127.0.0.1:...` address and opens a browser tab automatically:

```powershell
& "C:\Users\filip\Miniconda3\envs\expenses\python.exe" card_review_web.py --input outputs\ulp_2_50\anki_vocab_ulp_2_50_v4_reviewed_checkpoint.csv --needs-review outputs\ulp_2_50\anki_vocab_ulp_2_50_v4_needs_review.csv --rejected outputs\ulp_2_50\anki_vocab_ulp_2_50_v4_rejected.csv --output outputs\ulp_2_50\approved\anki_vocab_ulp_2_50_v4_approved.csv
```

For new split-review runs, also provide the generated pending queue:

```powershell
& "C:\Users\filip\Miniconda3\envs\expenses\python.exe" card_review_web.py --input outputs\ulp_2_50\reviewed_checkpoint.csv --needs-review outputs\ulp_2_50\reviewed_needs_review.csv --rejected outputs\ulp_2_50\reviewed_rejected.csv --output outputs\ulp_2_50\approved\approved.csv
```

When `--output` is omitted, the UI also defaults accepted exports to an
`approved/` folder beside the loaded episode artifacts. The interface lets you edit Ukrainian/English text, examples and sentence
targets, mark each row as `accept`, `needs_review`, or `reject`, and see
deterministic sentence-target warnings before export. `Save Review` writes a
manual-decision CSV; `Export Accepted` writes Anki-ready accepted cards using
the existing safe split-export logic.

To reopen a saved validation session:

```powershell
& "C:\Users\filip\Miniconda3\envs\expenses\python.exe" card_review_web.py --resume-session outputs\ulp_2_50\reviewed_manual_review.csv --output outputs\ulp_2_50\approved\approved.csv
```

Leave the terminal command running while reviewing cards and stop the local
server with `Ctrl+C` when finished. A browser interface is used because the
current Conda environment does not have a working Tcl/Tk runtime for desktop
Tkinter windows.

## Direct Export To Anki

Install the AnkiConnect add-on in Anki Desktop and leave Anki open while
pushing cards. The integration sends only already approved split exports:
vocabulary cards use `Basic (and reversed card)`, while sentence cards use
`Basic`.

Push existing approved v4 exports from the command line:

```powershell
python anki_connect.py --words outputs\ulp_2_50\approved\anki_vocab_ulp_2_50_v4_approved_words.csv --sentences outputs\ulp_2_50\approved\anki_vocab_ulp_2_50_v4_approved_sentences.csv --deck "Ukrainian::ULP 2-50"
```

Or enable the **Push Accepted to Anki** button when launching the review UI:

```powershell
python card_review_web.py --resume-session outputs\ulp_2_50\anki_vocab_ulp_2_50_v4_manual_review.csv --output outputs\ulp_2_50\approved\anki_vocab_ulp_2_50_v4_approved.csv --anki-deck "Ukrainian::ULP 2-50"
```

Anki uses `::` to create nested decks. Recommended local layout for this project:

```text
Ukrainian
  Ukrainian::Chapter 1 Book    # manual / textbook vocabulary
  Ukrainian::Podcast           # earlier podcast imports
  Ukrainian::ULP 2-50          # episode-specific approved exports
  Ukrainian::ULP 4-134
```

With `Ukrainian::ULP 2-50`, the deck screen shows a parent deck named `Ukrainian`
with an expandable `+` control; click it to reveal the child. For duplicate
cross-check before review, prefer `--crosscheck-anki-deck "Ukrainian"` so all
nested Ukrainian decks are included in one query.

Pushed notes receive stable integration tags plus searchable episode,
card-type, and source tags. Running the push again skips
already-pushed notes by default. Add `--update-existing` to `anki_connect.py`,
or `--anki-update-existing` to the UI command, to update notes previously
created through this integration. Cards manually imported before AnkiConnect
was introduced do not carry these tags and are not automatically matched for
updates.

## Cloud + Mobile Workflow Notes

**Without GCP credits, the practical path is weekend batch prep:** run
`--transcribe-only` for upcoming episodes, extract/review cards locally, push
approved decks to Anki, and open synced transcripts on your phone while listening.

Running in the cloud is mainly a convenience and automation upgrade: trigger
jobs from mobile, keep a queue running while your laptop is offline, and keep
logs/artifacts in one place. The current checkpointed pipeline already fits this
well if episode artifacts remain grouped under `outputs/<episode>/`.

For mobile-triggered runs, a practical rollout is:

- start with one cloud VM worker and durable storage for transcripts, logs, and
  checkpoints
- add an authenticated trigger (webhook/shortcut/bot) that enqueues one episode
- keep resume-first behavior so interrupted runs continue from the latest valid
  checkpoint

For final review and import from mobile, treat these as separate concerns:

- Final validation can be mobile-friendly by hosting the review UI behind auth.
- Direct Anki push still depends on where Anki is running.
  - AnkiConnect is desktop-local by default (`http://127.0.0.1:8765`).
  - If Anki is not running on the same machine, use file handoff (`*_words.csv`,
    `*_sentences.csv`, or `.apkg`) and import later.
  - A one-tap remote push is possible with a secure home bridge that forwards to
    local AnkiConnect, but this is an advanced setup.

There is no stable, official "write cards to AnkiWeb from any server" workflow
documented for this project; keep cloud delivery focused on approved exports and
explicit user import unless a bridge path is deliberately configured.

If a batched extraction fails after writing a checkpoint, resume from it without
repeating completed batches:

```powershell
python episode_to_anki.py --transcript-file transcript.txt --resume-input outputs\ulp_2_50\partial.csv --resume-after-batch 5 --output outputs\ulp_2_50\resumed.csv --batch-chars 1200 --cards-per-batch 6
```

## On-Demand Transcript From Phone (Google Cloud, Optional)

GCP deployment is **on hold** when free credits are unavailable. The code below
is kept for future use; until then, use `--transcribe-only` batch prep above.

For listening support away from the PC, a small Cloud Run service can transcribe
an episode on demand and email a download link:

```text
Phone shortcut -> Cloud Run -> Speech-to-Text -> GCS cache -> email signed URL
```

This path is **transcript delivery only**. It does not run vocabulary extraction
or Anki review. Local `faster-whisper` caching under `transcripts/` remains the
offline Anki pipeline.

Package: `cloud/transcript_service/`. Deploy steps: [`cloud/deploy.md`](cloud/deploy.md).

Local dry run with mock speech (no GCP):

```powershell
cd cloud
pip install -r requirements.txt
$env:TRANSCRIPT_API_TOKEN="dev-token"
$env:EMAIL_TO="you@example.com"
$env:TRANSCRIPT_SPEECH_BACKEND="mock"
$env:LOCAL_DATA_DIR="..\cloud_data"
uvicorn transcript_service.app:app --reload --port 8080
```

Request the newest episode:

```powershell
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8080/v1/transcripts `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{ "episode_index": 0 }'
```

Phone shortcut: `POST /v1/transcripts` with the same bearer token and body
`{ "episode_index": 0 }` or `{ "title_search": "doctor" }`. Open the emailed
signed URL when the job completes (or poll `GET /v1/jobs/{job_id}`).

## Optional OpenAI Mode

OpenAI extraction and review are available when you have API credits
(separate from a ChatGPT subscription). Prefer this when you want faster runs
than local `qwen3:4b`.

1. Add a few euros of prepaid credit at https://platform.openai.com/settings/organization/billing
2. Create an API key and set it in PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-..."
```

Full extract + combined review (typically a handful of API calls, not dozens):

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py `
  --transcript-file transcripts\ULP_4-134.txt `
  --episode-name "ULP 4-134" `
  --vocab-provider openai `
  --review-provider openai `
  --review-cards `
  --text-model gpt-4o-mini `
  --batch-chars 4000 `
  --cards-per-batch 12 `
  --card-format bidirectional-with-sentences `
  --crosscheck-anki `
  --crosscheck-anki-deck "Ukrainian" `
  --output outputs\ulp_4_134_openai\anki_vocab.csv `
  --log-file logs\ulp_4_134_openai.log
```

Notes:

- OpenAI review uses one combined triage+lexical pass (default batch size 60).
- With `--review-provider openai` and `--crosscheck-anki`, matched Anki cards are
  skipped by default unless you set `--duplicate-policy` explicitly.
- Optional `--synthesize-examples` (OpenAI only): after review, invent short natural
  Ukrainian sentences for needs-review cards that lack a safe sentence target. Valid
  fills stay in needs-review with reason `synthetic example ready for confirmation`
  so you can Accept quickly. The dashboard Generate checkbox **Fill missing examples
  (OpenAI)** turns this on (default checked for OpenAI).
- Prefer `--crosscheck-anki-deck "Ukrainian"` so nested book/podcast/episode
  decks are all included.
- Re-review an existing extraction without re-extracting:

```powershell
python episode_to_anki.py `
  --review-input outputs\ulp_4_134\anki_vocab_ulp_4_134_extracted.csv `
  --review-provider openai `
  --text-model gpt-4o-mini `
  --crosscheck-anki `
  --crosscheck-anki-deck "Ukrainian" `
  --output outputs\ulp_4_134_openai\reviewed.csv `
  --card-format bidirectional-with-sentences
```

Transcription-only via OpenAI Whisper (optional; local `faster-whisper` remains free):

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --transcriber openai --transcription-model whisper-1 --vocab-provider openai --output outputs\ulp_3_103\anki_vocab.csv
```

The local Ollama route does not require an API key.
