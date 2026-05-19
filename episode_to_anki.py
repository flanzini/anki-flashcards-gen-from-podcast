"""Create Ukrainian vocabulary Anki cards from a podcast episode transcript.

Workflow:
1. Use an existing transcript, local MP3, or RSS episode.
2. Transcribe audio with OpenAI, faster-whisper, or a local Whisper CLI.
3. Extract useful Ukrainian vocabulary and English translations with OpenAI or
   a local Ollama model.
4. Write an Anki-ready CSV.

This script does not pretend that vocabulary exists in the RSS feed. It derives
cards from transcript text.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from podcast_to_anki import DEFAULT_RSS_URL, Episode, fetch_text, parse_rss


USER_AGENT = "Mozilla/5.0 (compatible; episode-to-anki/1.0)"
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
SPACES_RE = re.compile(r"\s+")
DEFAULT_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_LOCAL_TRANSCRIPTION_MODEL = "medium"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


@dataclass(frozen=True)
class VocabCard:
    ukrainian: str
    english: str
    example: str
    tags: str
    source: str
    episode: str


def select_episode(
    episodes: Sequence[Episode],
    *,
    index: int,
    title_search: str = "",
) -> Episode:
    if title_search:
        lowered = title_search.casefold()
        matches = [episode for episode in episodes if lowered in episode.title.casefold()]
        if not matches:
            raise ValueError(f"No RSS episode title matched {title_search!r}.")
        return matches[0]

    if index < 0 or index >= len(episodes):
        raise IndexError(f"Episode index {index} is out of range. Feed has {len(episodes)} episodes.")
    return episodes[index]


def safe_filename(value: str, suffix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")
    return f"{cleaned[:90] or 'episode'}{suffix}"


def download_file(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        with output_path.open("wb") as output_file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output_file.write(chunk)
    return output_path


def transcribe_openai(audio_path: Path, *, model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY before using OpenAI transcription.")

    fields = {
        "model": model,
        "language": "uk",
        "response_format": "text",
    }
    body, content_type = build_multipart_body(fields, "file", audio_path)
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI transcription failed: {message}") from exc


def build_multipart_body(fields: dict, file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----episodeToAnki{os.urandom(12).hex()}"
    chunks: List[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                f"{value}\r\n".encode("utf-8"),
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def transcribe_whisper_cli(audio_path: Path, *, output_dir: Path, model: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "whisper",
        str(audio_path),
        "--language",
        "Ukrainian",
        "--task",
        "transcribe",
        "--model",
        model,
        "--output_format",
        "txt",
        "--output_dir",
        str(output_dir),
    ]
    subprocess.run(command, check=True)
    transcript_path = output_dir / f"{audio_path.stem}.txt"
    if not transcript_path.exists():
        raise FileNotFoundError(f"Whisper did not create {transcript_path}.")
    return transcript_path.read_text(encoding="utf-8")


def transcribe_faster_whisper(audio_path: Path, *, model: str) -> str:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: "
            'conda run -n expenses pip install faster-whisper'
        ) from exc

    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = whisper.transcribe(
        str(audio_path),
        language="uk",
        beam_size=5,
        vad_filter=True,
    )
    print(f"Detected language: {info.language} ({info.language_probability:.2f})")
    return "\n".join(clean_text(segment.text) for segment in segments if clean_text(segment.text))


def transcribe_audio(
    audio_path: Path,
    *,
    provider: str,
    output_dir: Path,
    model: str,
) -> str:
    if provider == "openai":
        return transcribe_openai(audio_path, model=model)
    if provider == "whisper-cli":
        return transcribe_whisper_cli(audio_path, output_dir=output_dir, model=model)
    if provider == "faster-whisper":
        return transcribe_faster_whisper(audio_path, model=model)
    raise ValueError(f"Unknown transcriber: {provider}")


def extract_vocab_openai(
    transcript: str,
    *,
    api_key: str,
    model: str,
    max_cards: int,
    episode: str,
    source: str,
) -> List[VocabCard]:
    prompt = build_vocab_prompt(transcript, max_cards=max_cards)
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You create high-quality Anki vocabulary cards for English-speaking "
                    "learners of Ukrainian. Extract useful words and short phrases from "
                    "the transcript. Prefer lemmas, common phrases, and learner-useful "
                    "items over names, filler words, and one-off proper nouns."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI vocab extraction failed: {message}") from exc

    content = extract_response_text(data)
    return cards_from_json_text(content, source=source, episode=episode)


def extract_vocab_ollama(
    transcript: str,
    *,
    model: str,
    max_cards: int,
    episode: str,
    source: str,
    url: str,
) -> List[VocabCard]:
    prompt = build_vocab_prompt(transcript, max_cards=max_cards)
    payload = {
        "model": model,
        "prompt": (
            "You create high-quality Anki vocabulary cards for English-speaking "
            "learners of Ukrainian. Return only valid JSON.\n\n"
            f"{prompt}"
        ),
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 8192,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Could not reach Ollama. Make sure Ollama is installed and running, "
            f"then pull the model with: ollama pull {model}"
        ) from exc

    content = data.get("response") or data.get("thinking") or ""
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama returned no response: {data}")
    return cards_from_json_text(content, source=source, episode=episode)


def extract_vocab(
    transcript: str,
    *,
    provider: str,
    model: str,
    max_cards: int,
    episode: str,
    source: str,
    ollama_url: str,
) -> List[VocabCard]:
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY before using OpenAI vocabulary extraction.")
        return extract_vocab_openai(
            transcript,
            api_key=api_key,
            model=model,
            max_cards=max_cards,
            episode=episode,
            source=source,
        )
    if provider == "ollama":
        return extract_vocab_ollama(
            transcript,
            model=model,
            max_cards=max_cards,
            episode=episode,
            source=source,
            url=ollama_url,
        )
    raise ValueError(f"Unknown vocabulary provider: {provider}")


def cards_from_json_text(content: str, *, source: str, episode: str) -> List[VocabCard]:
    parsed = parse_json_object(content)
    rows = parsed.get("cards", [])
    cards: List[VocabCard] = []
    seen = set()

    for row in rows:
        ukrainian = clean_text(str(row.get("ukrainian", "")))
        english = clean_text(str(row.get("english", "")))
        example = clean_text(str(row.get("example", "")))
        key = ukrainian.casefold()
        if not ukrainian or not english or key in seen or not CYRILLIC_RE.search(ukrainian):
            continue
        seen.add(key)
        cards.append(
            VocabCard(
                ukrainian=ukrainian,
                english=english,
                example=example,
                tags="ukrainian podcast transcript vocab",
                source=source,
                episode=episode,
            )
        )

    return cards


def parse_json_object(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(content[start : end + 1])


def build_vocab_prompt(transcript: str, *, max_cards: int) -> str:
    clipped = transcript[:45000]
    return (
        f"Extract up to {max_cards} Ukrainian vocabulary flashcards from this transcript.\n"
        "Return only JSON in this exact shape:\n"
        '{"cards":[{"ukrainian":"...","english":"...","example":"..."}]}\n\n'
        "Rules:\n"
        "- ukrainian: one Ukrainian word, lemma, or short phrase from the transcript.\n"
        "- english: concise English meaning or translation.\n"
        "- example: a short Ukrainian example phrase from the transcript when possible.\n"
        "- Avoid duplicate inflected forms unless they teach a distinct phrase.\n"
        "- Avoid personal names, episode titles, URLs, ads, and grammar metalanguage.\n\n"
        f"Transcript:\n{clipped}"
    )


def extract_response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]

    parts: List[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    if parts:
        return "".join(parts)
    raise ValueError("Could not find text in OpenAI response.")


def clean_text(value: str) -> str:
    return SPACES_RE.sub(" ", value).strip(" \"'.,;:()[]{}")


def write_cards(cards: Iterable[VocabCard], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Ukrainian", "English", "Example", "Tags", "Source", "Episode"])
        for card in cards:
            writer.writerow(
                [card.ukrainian, card.english, card.example, card.tags, card.source, card.episode]
            )
            count += 1
    return count


def read_or_create_transcript(args: argparse.Namespace, episode: Optional[Episode]) -> tuple[str, str, str]:
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        episode_name = args.episode_name or transcript_path.stem
        return transcript_path.read_text(encoding="utf-8-sig"), str(transcript_path), episode_name

    audio_path = Path(args.audio_file) if args.audio_file else None
    episode_name = args.episode_name or (episode.title if episode else "episode")
    source = str(audio_path) if audio_path else ""

    if audio_path is None:
        if episode is None:
            raise ValueError("Provide --transcript-file, --audio-file, or RSS selection arguments.")
        if not episode.audio_url:
            raise ValueError(f"RSS episode has no audio enclosure: {episode.title}")
        audio_path = args.audio_dir / safe_filename(episode.title, ".mp3")
        source = episode.link or episode.audio_url
        if not audio_path.exists() or args.force_download:
            print(f"Downloading audio: {episode.title}")
            download_file(episode.audio_url, audio_path)

    transcript_path = args.transcript_dir / safe_filename(episode_name, ".txt")
    if transcript_path.exists() and not args.force_transcribe:
        return transcript_path.read_text(encoding="utf-8-sig"), source, episode_name

    print(f"Transcribing audio with {args.transcriber}: {audio_path}")
    transcript = transcribe_audio(
        audio_path,
        provider=args.transcriber,
        output_dir=args.transcript_dir,
        model=args.transcription_model,
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(transcript, encoding="utf-8")
    return transcript, source, episode_name


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Anki vocab cards from a podcast transcript.")
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL, help="Podcast RSS feed URL.")
    parser.add_argument("--episode-index", type=int, default=0, help="RSS episode index, newest is 0.")
    parser.add_argument("--title-search", default="", help="Pick the first RSS episode title containing this text.")
    parser.add_argument("--audio-file", type=Path, help="Use a local audio file instead of RSS download.")
    parser.add_argument("--transcript-file", type=Path, help="Use an existing transcript and skip transcription.")
    parser.add_argument("--episode-name", default="", help="Name to store in the Episode CSV column.")
    parser.add_argument("--audio-dir", type=Path, default=Path("audio"), help="Where downloaded audio goes.")
    parser.add_argument(
        "--transcript-dir", type=Path, default=Path("transcripts"), help="Where transcripts are cached."
    )
    parser.add_argument("--output", type=Path, default=Path("anki_from_transcript.csv"))
    parser.add_argument("--max-cards", type=int, default=60)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument(
        "--transcriber",
        choices=["faster-whisper", "openai", "whisper-cli"],
        default="faster-whisper",
    )
    parser.add_argument(
        "--transcription-model",
        default=DEFAULT_LOCAL_TRANSCRIPTION_MODEL,
        help="Use medium/large-v3 for faster-whisper, whisper-1 for OpenAI, or a Whisper CLI model name.",
    )
    parser.add_argument(
        "--vocab-provider",
        choices=["ollama", "openai"],
        default="ollama",
        help="Use ollama for fully local extraction or openai for API extraction.",
    )
    parser.add_argument(
        "--ollama-model",
        default=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        help="Local Ollama model for vocabulary extraction.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
        help="Ollama generate API URL.",
    )
    parser.add_argument("--text-model", default=os.environ.get("OPENAI_TEXT_MODEL", DEFAULT_TEXT_MODEL))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    started = time.time()

    try:
        episode = None
        if not args.audio_file and not args.transcript_file:
            episodes = parse_rss(fetch_text(args.rss_url))
            episode = select_episode(
                episodes,
                index=args.episode_index,
                title_search=args.title_search,
            )

        if args.transcriber == "openai" and args.transcription_model == DEFAULT_LOCAL_TRANSCRIPTION_MODEL:
            args.transcription_model = DEFAULT_TRANSCRIPTION_MODEL

        transcript, source, episode_name = read_or_create_transcript(args, episode)
        vocab_model = args.ollama_model if args.vocab_provider == "ollama" else args.text_model
        cards = extract_vocab(
            transcript,
            provider=args.vocab_provider,
            model=vocab_model,
            max_cards=args.max_cards,
            episode=episode_name,
            source=source,
            ollama_url=args.ollama_url,
        )
        count = write_cards(cards, args.output)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {count} vocabulary cards to {args.output}.")
    print(f"Elapsed: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
