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
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --episode-name "ULP 3-103" --output anki_vocab.csv
```

This uses:

- `faster-whisper` with model `medium` for Ukrainian transcription
- Ollama model `qwen3:4b` for vocabulary extraction and English translation

The transcript is cached in `transcripts/`, so reruns do not retranscribe unless
you add `--force-transcribe`.

## Existing Transcript To Anki

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --transcript-file transcript.txt --episode-name "ULP 3-103" --output anki_vocab.csv
```

## RSS Episode To Anki

Newest episode from the default Ukrainian Lessons Podcast RSS feed:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --episode-index 0 --output anki_vocab.csv
```

Find by title text:

```powershell
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --title-search "doctor" --output anki_vocab.csv
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
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --transcript-file transcripts\ULP_3-103.txt --episode-name "ULP 3-103" --output anki_vocab.csv --max-cards 40 --batch-chars 4000 --cards-per-batch 8 --ollama-timeout 900 --log-file logs\episode_103.log
```

The script writes the output CSV after every completed batch, so an interrupted
run still leaves partial cards in the output file.

## Anki Import

The generated CSV columns are:

```text
Ukrainian, English, Example, Tags, Source, Episode
```

In Anki, map:

- `Ukrainian` to the front field
- `English` to the back field
- `Example` to an extra field if your note type has one
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
--card-format bidirectional-with-sentences --output anki_vocab_ulp_2_50.csv
```

This writes two CSV files:

```text
anki_vocab_ulp_2_50_words.csv
anki_vocab_ulp_2_50_sentences.csv
```

Import `*_words.csv` using Anki's `Basic (and reversed card)` note type. The
word cards contain only Ukrainian and English, so the English-to-Ukrainian
prompt does not reveal its answer. Import `*_sentences.csv` using the
one-direction `Basic` note type; its fronts are Ukrainian fill-the-gap prompts.

To export these two files from an already generated or reviewed CSV without
calling the model again:

```powershell
python episode_to_anki.py --format-input reviewed.csv --output anki_vocab_ulp_2_50.csv --card-format bidirectional-with-sentences
```

## Review / Validate Generated Cards

For better quality, add a second local Ollama review pass:

```powershell
--review-cards --review-batch-size 20
```

You can also review an existing generated CSV without retranscribing or
extracting again:

```powershell
C:\Users\filip\Miniconda3\envs\expenses\python.exe episode_to_anki.py --review-input anki_vocab_ulp_2_49.csv --output anki_vocab_ulp_2_49_reviewed.csv --card-format basic-with-sentences --ollama-timeout 900 --log-file logs\episode_2_49_review.log
```

The reviewer asks the local model to drop bad cards, normalize Ukrainian terms
to useful learner forms, fix concise translations, and remove garbled examples.
When `--review-cards` is used during extraction, the unreviewed extracted cards
are also saved to an `_extracted.csv` snapshot before review starts.

If a batched extraction fails after writing a checkpoint, resume from it without
repeating completed batches:

```powershell
python episode_to_anki.py --transcript-file transcript.txt --resume-input partial.csv --resume-after-batch 5 --output resumed.csv --batch-chars 1200 --cards-per-batch 6
```

## Optional OpenAI Mode

OpenAI remains available if you want it later:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --transcriber openai --transcription-model whisper-1 --vocab-provider openai --output anki_vocab.csv
```

The local Ollama route does not require an API key.
