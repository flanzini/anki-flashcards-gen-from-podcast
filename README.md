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

## Optional OpenAI Mode

OpenAI remains available if you want it later:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
& "C:\Users\filip\Miniconda3\condabin\conda.bat" run -n expenses python episode_to_anki.py --audio-file ULP_3_103.mp3 --transcriber openai --transcription-model whisper-1 --vocab-provider openai --output anki_vocab.csv
```

The local Ollama route does not require an API key.
