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

Anki uses `::` to create nested decks. With `Ukrainian::ULP 2-50`, the deck
screen shows a parent deck named `Ukrainian` with an expandable `+` control;
click it to reveal the child `ULP 2-50`. For podcast-only grouping under an
existing parent, use a name such as `Ukrainian Podcast::ULP 2-50` instead.

Pushed notes receive stable integration tags plus searchable episode,
card-type, and source tags. Running the push again skips
already-pushed notes by default. Add `--update-existing` to `anki_connect.py`,
or `--anki-update-existing` to the UI command, to update notes previously
created through this integration. Cards manually imported before AnkiConnect
was introduced do not carry these tags and are not automatically matched for
updates.

If a batched extraction fails after writing a checkpoint, resume from it without
repeating completed batches:

```powershell
python episode_to_anki.py --transcript-file transcript.txt --resume-input outputs\ulp_2_50\partial.csv --resume-after-batch 5 --output outputs\ulp_2_50\resumed.csv --batch-chars 1200 --cards-per-batch 6
```

## Optional OpenAI Mode

OpenAI remains available if you want it later:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --transcriber openai --transcription-model whisper-1 --vocab-provider openai --output outputs\ulp_3_103\anki_vocab.csv
```

The local Ollama route does not require an API key.
