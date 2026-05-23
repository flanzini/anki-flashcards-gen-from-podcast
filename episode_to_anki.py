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
import logging
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
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
DEFAULT_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_LOCAL_TRANSCRIPTION_MODEL = "medium"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OLLAMA_TIMEOUT = 900
DEFAULT_OLLAMA_NUM_PREDICT = 2048
DEFAULT_OLLAMA_RETRIES = 2
DEFAULT_BATCH_CHARS = 5000
DEFAULT_CARDS_PER_BATCH = 10

LOGGER = logging.getLogger("episode_to_anki")


@dataclass(frozen=True)
class VocabCard:
    ukrainian: str
    english: str
    example: str
    tags: str
    source: str
    episode: str
    note: str = ""


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
    LOGGER.info("Downloading %s -> %s", url, output_path)
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
        LOGGER.info("Requesting OpenAI transcription with model=%s", model)
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
    LOGGER.info("Running Whisper CLI transcription with model=%s", model)
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

    LOGGER.info("Loading faster-whisper model=%s on CPU with int8 compute", model)
    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    LOGGER.info("Starting faster-whisper transcription for %s", audio_path)
    segments, info = whisper.transcribe(
        str(audio_path),
        language="uk",
        beam_size=5,
        vad_filter=True,
    )
    LOGGER.info("Detected language: %s (%.2f)", info.language, info.language_probability)

    lines: List[str] = []
    last_reported = -1
    for segment in segments:
        text = clean_text(segment.text)
        if not text:
            continue
        lines.append(text)
        current_minute = int(segment.end // 60)
        if current_minute != last_reported:
            LOGGER.info("Transcribed through %.1f minutes", segment.end / 60)
            last_reported = current_minute

    LOGGER.info("Finished transcription with %d text segments", len(lines))
    return "\n".join(lines)


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
    timeout: int,
    num_predict: int,
    retries: int,
) -> List[VocabCard]:
    prompt = build_vocab_prompt(transcript, max_cards=max_cards)
    LOGGER.info(
        "Requesting Ollama vocabulary extraction: model=%s chars=%d max_cards=%d timeout=%ss num_predict=%d",
        model,
        len(transcript),
        max_cards,
        timeout,
        num_predict,
    )
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
            "num_predict": num_predict,
        },
    }
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama vocabulary extraction timed out after {timeout}s. "
                "Try smaller --batch-chars/--cards-per-batch values or increase --ollama-timeout."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Could not reach Ollama. Make sure Ollama is installed and running, "
                f"then pull the model with: ollama pull {model}"
            ) from exc

        content = data.get("response") or data.get("thinking") or ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Ollama returned no response: {data}")
        try:
            cards = cards_from_json_text(content, source=source, episode=episode)
        except json.JSONDecodeError as exc:
            if attempt < retries:
                LOGGER.warning(
                    "Ollama returned malformed vocabulary JSON; retrying request (%d/%d)",
                    attempt + 1,
                    retries,
                )
                continue
            raise RuntimeError("Ollama repeatedly returned malformed JSON during vocabulary extraction.") from exc
        LOGGER.info("Ollama returned %d usable cards", len(cards))
        return cards
    raise RuntimeError("Ollama vocabulary extraction retry loop exited unexpectedly.")


def extract_vocab(
    transcript: str,
    *,
    provider: str,
    model: str,
    max_cards: int,
    episode: str,
    source: str,
    ollama_url: str,
    ollama_timeout: int,
    ollama_num_predict: int,
    ollama_retries: int,
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
            timeout=ollama_timeout,
            num_predict=ollama_num_predict,
            retries=ollama_retries,
        )
    raise ValueError(f"Unknown vocabulary provider: {provider}")


def extract_vocab_batched(
    transcript: str,
    *,
    provider: str,
    model: str,
    max_cards: int,
    cards_per_batch: int,
    batch_chars: int,
    episode: str,
    source: str,
    ollama_url: str,
    ollama_timeout: int,
    ollama_num_predict: int,
    ollama_retries: int,
    initial_cards: Sequence[VocabCard] = (),
    resume_after_batch: int = 0,
    checkpoint_output: Optional[Path] = None,
    card_format: str = "fields",
) -> List[VocabCard]:
    batches = split_transcript(transcript, max_chars=batch_chars)
    if not batches:
        return []

    LOGGER.info(
        "Extracting vocabulary in %d batch(es), batch_chars=%d, cards_per_batch=%d, total_limit=%d",
        len(batches),
        batch_chars,
        cards_per_batch,
        max_cards,
    )
    all_cards = dedupe_cards(initial_cards)
    seen = {card.ukrainian.casefold() for card in all_cards}
    if resume_after_batch:
        LOGGER.info(
            "Resuming extraction after batch %d with %d checkpointed vocabulary items",
            resume_after_batch,
            len(all_cards),
        )

    for index, batch in enumerate(batches, start=1):
        if index <= resume_after_batch:
            LOGGER.info("Batch %d/%d already checkpointed; skipping", index, len(batches))
            continue
        remaining = max_cards - len(all_cards)
        if remaining <= 0:
            LOGGER.info("Reached max card limit (%d); skipping remaining batches", max_cards)
            break

        batch_limit = min(cards_per_batch, remaining)
        LOGGER.info(
            "Batch %d/%d: extracting up to %d cards from %d chars",
            index,
            len(batches),
            batch_limit,
            len(batch),
        )
        batch_cards = extract_vocab(
            batch,
            provider=provider,
            model=model,
            max_cards=batch_limit,
            episode=episode,
            source=source,
            ollama_url=ollama_url,
            ollama_timeout=ollama_timeout,
            ollama_num_predict=ollama_num_predict,
            ollama_retries=ollama_retries,
        )

        added = 0
        for card in batch_cards:
            key = card.ukrainian.casefold()
            if key in seen:
                continue
            seen.add(key)
            all_cards.append(card)
            added += 1

        LOGGER.info(
            "Batch %d/%d complete: %d new cards, %d total",
            index,
            len(batches),
            added,
            len(all_cards),
        )
        if checkpoint_output:
            count = write_cards(all_cards, checkpoint_output, card_format=card_format)
            LOGGER.info("Checkpoint written: %s (%d cards)", checkpoint_output, count)
            if card_format == "bidirectional-with-sentences":
                fields_checkpoint = suffixed_output_path(checkpoint_output, "checkpoint")
                write_cards(all_cards, fields_checkpoint, card_format="fields")
                LOGGER.info("Fields checkpoint written for resume: %s", fields_checkpoint)

    return all_cards


def review_cards_ollama(
    cards: Sequence[VocabCard],
    *,
    model: str,
    url: str,
    timeout: int,
    num_predict: int,
    retries: int,
    batch_size: int,
    checkpoint_output: Optional[Path] = None,
    card_format: str = "fields",
) -> List[VocabCard]:
    if not cards:
        return []

    reviewed: List[VocabCard] = []
    batches = [list(cards[index : index + batch_size]) for index in range(0, len(cards), batch_size)]
    LOGGER.info("Reviewing %d vocabulary items in %d batch(es)", len(cards), len(batches))

    for index, batch in enumerate(batches, start=1):
        LOGGER.info("Review batch %d/%d: %d cards", index, len(batches), len(batch))
        prompt = build_review_prompt(batch)
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 8192,
                "num_predict": num_predict,
            },
        }
        for attempt in range(retries + 1):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Ollama card review timed out after {timeout}s. "
                    "Try a smaller --review-batch-size value or increase --ollama-timeout."
                ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError("Could not reach Ollama during card review.") from exc

            content = data.get("response") or data.get("thinking") or ""
            try:
                batch_reviewed = reviewed_cards_from_json(content, batch)
            except json.JSONDecodeError as exc:
                if attempt < retries:
                    LOGGER.warning(
                        "Ollama returned malformed review JSON; retrying request (%d/%d)",
                        attempt + 1,
                        retries,
                    )
                    continue
                raise RuntimeError("Ollama repeatedly returned malformed JSON during card review.") from exc
            reviewed.extend(batch_reviewed)
            break
        reviewed = dedupe_cards(reviewed)
        LOGGER.info("Review batch %d/%d complete: %d kept so far", index, len(batches), len(reviewed))
        if checkpoint_output:
            count = write_cards(reviewed, checkpoint_output, card_format=card_format)
            LOGGER.info("Review checkpoint written: %s (%d rows)", checkpoint_output, count)
            if card_format == "bidirectional-with-sentences":
                fields_checkpoint = suffixed_output_path(checkpoint_output, "reviewed_checkpoint")
                write_cards(reviewed, fields_checkpoint, card_format="fields")
                LOGGER.info("Fields review checkpoint written for resume: %s", fields_checkpoint)

    return dedupe_cards(reviewed)


def build_review_prompt(cards: Sequence[VocabCard]) -> str:
    payload = [
        {
            "id": index,
            "ukrainian": card.ukrainian,
            "english": card.english,
            "example": card.example,
        }
        for index, card in enumerate(cards)
    ]
    return (
        "You are reviewing Ukrainian Anki vocabulary cards for an English-speaking learner.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"decision":"keep|edit|drop","ukrainian":"...","english":"...","example":"...","note":"..."}]}\n\n'
        "Review criteria:\n"
        "- keep only correct, natural, useful Ukrainian vocabulary cards.\n"
        "- drop ads/outro/social-media/membership cards, URLs, names, malformed transcript artifacts, and transliterated English.\n"
        "- drop grammar metalanguage unless it is a central lesson term.\n"
        "- edit inflected forms to dictionary forms when appropriate.\n"
        "- fix concise English translations.\n"
        "- keep examples only if they are natural Ukrainian and actually contain or illustrate the term.\n"
        "- if an example is garbled or irrelevant, set example to an empty string.\n"
        "- if unsure, drop the card.\n\n"
        f"Cards to review:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def reviewed_cards_from_json(content: str, original_cards: Sequence[VocabCard]) -> List[VocabCard]:
    parsed = parse_json_object(content)
    rows = parsed.get("cards", [])
    original_by_id = {index: card for index, card in enumerate(original_cards)}
    reviewed: List[VocabCard] = []

    for row in rows:
        try:
            original = original_by_id[int(row.get("id"))]
        except (TypeError, ValueError, KeyError):
            continue

        decision = str(row.get("decision", "")).casefold()
        if decision == "drop":
            continue
        if decision not in {"keep", "edit"}:
            continue

        ukrainian = clean_text(str(row.get("ukrainian") or original.ukrainian))
        english = clean_text(str(row.get("english") or original.english))
        example = clean_text(str(row.get("example") or ""))
        note = clean_text(str(row.get("note") or original.note))

        if not ukrainian or not english or not CYRILLIC_RE.search(ukrainian):
            continue

        reviewed.append(
            VocabCard(
                ukrainian=ukrainian,
                english=english,
                example=example,
                tags=f"{original.tags} reviewed",
                source=original.source,
                episode=original.episode,
                note=note,
            )
        )

    return reviewed


def dedupe_cards(cards: Iterable[VocabCard]) -> List[VocabCard]:
    unique: List[VocabCard] = []
    seen = set()
    for card in cards:
        key = card.ukrainian.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique


def split_transcript(transcript: str, *, max_chars: int) -> List[str]:
    transcript = transcript.strip()
    if not transcript:
        return []
    if max_chars <= 0 or len(transcript) <= max_chars:
        return [transcript]

    pieces = [piece.strip() for piece in SENTENCE_SPLIT_RE.split(transcript) if piece.strip()]
    batches: List[str] = []
    current: List[str] = []
    current_len = 0

    for piece in pieces:
        piece_len = len(piece) + 1
        if current and current_len + piece_len > max_chars:
            batches.append("\n".join(current))
            current = []
            current_len = 0

        if piece_len > max_chars:
            for start in range(0, len(piece), max_chars):
                chunk = piece[start : start + max_chars].strip()
                if chunk:
                    batches.append(chunk)
            continue

        current.append(piece)
        current_len += piece_len

    if current:
        batches.append("\n".join(current))
    return batches


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
                note=clean_text(str(row.get("note", ""))),
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
        '{"cards":[{"ukrainian":"...","english":"...","example":"...","note":"..."}]}\n\n'
        "Rules:\n"
        "- Extract only useful Ukrainian vocabulary for an English-speaking learner.\n"
        "- ukrainian: a normal Ukrainian dictionary form, common phrase, or lesson phrase.\n"
        "- english: concise English meaning or translation.\n"
        "- example: a short natural Ukrainian example phrase from the transcript when possible.\n"
        "- note: optional brief usage note, otherwise empty string.\n"
        "- Prefer food, recipe, cooking, quantity, and core episode-topic vocabulary.\n"
        "- Prefer lemmas: use готувати, not готувала; цукор, not цукру, unless the inflected form is the teaching point.\n"
        "- Include grammar terms only if they are central lesson terms, e.g. родовий відмінок.\n"
        "- Exclude names, URLs, episode titles, intro/outro phrases, ads, membership/social-media language.\n"
        "- Exclude malformed transcript artifacts, mixed-language fragments, transliterated English, and unnatural phrases.\n"
        "- Avoid duplicate inflected forms unless they teach a distinct phrase.\n"
        "- If uncertain about a card, omit it.\n\n"
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


def maybe_repair_mojibake(text: str, mode: str) -> str:
    if mode == "never":
        return text
    if mode == "auto" and not looks_mojibaked(text):
        return text

    repaired = repair_mojibake(text)
    if repaired != text and cyrillic_score(repaired) > cyrillic_score(text):
        LOGGER.info(
            "Repaired likely mojibake transcript text (Cyrillic chars: %d -> %d)",
            cyrillic_score(text),
            cyrillic_score(repaired),
        )
        return repaired

    if mode == "always":
        LOGGER.warning("Mojibake repair was requested but did not improve Cyrillic content")
    return text


def looks_mojibaked(text: str) -> bool:
    sample = text[:5000]
    mojibake_markers = sample.count("Ð") + sample.count("Ñ") + sample.count("Â")
    return mojibake_markers > max(5, cyrillic_score(sample))


def repair_mojibake(text: str) -> str:
    try:
        return text.encode("cp1252", errors="replace").decode("utf-8", errors="replace")
    except UnicodeError:
        return text


def cyrillic_score(text: str) -> int:
    return sum(1 for ch in text if CYRILLIC_RE.match(ch))


def write_cards(
    cards: Iterable[VocabCard],
    output_path: Path,
    *,
    card_format: str = "fields",
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cards = list(cards)
    if card_format == "fields":
        return write_field_cards(cards, output_path)
    if card_format == "basic":
        return write_basic_cards(cards, output_path, include_sentence_cards=False)
    if card_format == "basic-with-sentences":
        return write_basic_cards(cards, output_path, include_sentence_cards=True)
    if card_format == "bidirectional-with-sentences":
        return write_split_study_cards(cards, output_path)
    raise ValueError(f"Unknown card format: {card_format}")


def write_field_cards(cards: Iterable[VocabCard], output_path: Path) -> int:
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


def write_basic_cards(
    cards: Iterable[VocabCard],
    output_path: Path,
    *,
    include_sentence_cards: bool,
) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Front", "Back", "Tags", "Source", "Episode", "CardType"])
        for card in cards:
            writer.writerow(
                [
                    card.ukrainian,
                    build_vocab_back(card),
                    card.tags,
                    card.source,
                    card.episode,
                    "vocabulary",
                ]
            )
            count += 1

            sentence_front = build_sentence_front(card.example, card.ukrainian)
            if include_sentence_cards and sentence_front:
                writer.writerow(
                    [
                        sentence_front,
                        f"{highlight_term(card.example, card.ukrainian)}<br><br>{card.ukrainian} - {card.english}",
                        f"{card.tags} sentence",
                        card.source,
                        card.episode,
                        "sentence",
                    ]
                )
                count += 1
    return count


def write_split_study_cards(cards: Iterable[VocabCard], output_path: Path) -> int:
    cards = list(cards)
    words_path = suffixed_output_path(output_path, "words")
    sentences_path = suffixed_output_path(output_path, "sentences")
    word_count = write_bidirectional_word_cards(cards, words_path)
    sentence_count = write_sentence_cards(cards, sentences_path)
    LOGGER.info("Split study files written: %s and %s", words_path, sentences_path)
    return word_count + sentence_count


def suffixed_output_path(output_path: Path, suffix: str) -> Path:
    file_suffix = output_path.suffix or ".csv"
    return output_path.with_name(f"{output_path.stem}_{suffix}{file_suffix}")


def write_bidirectional_word_cards(cards: Iterable[VocabCard], output_path: Path) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Front", "Back", "Tags", "Source", "Episode", "CardType"])
        for card in cards:
            writer.writerow([card.ukrainian, card.english, card.tags, card.source, card.episode, "vocabulary"])
            count += 1
    return count


def write_sentence_cards(cards: Iterable[VocabCard], output_path: Path) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Front", "Back", "Tags", "Source", "Episode", "CardType"])
        for card in cards:
            sentence_front = build_sentence_front(card.example, card.ukrainian)
            if not sentence_front:
                continue
            writer.writerow(
                [
                    sentence_front,
                    f"{highlight_term(card.example, card.ukrainian)}<br><br>{card.ukrainian} - {card.english}",
                    f"{card.tags} sentence",
                    card.source,
                    card.episode,
                    "sentence",
                ]
            )
            count += 1
    return count


def read_cards_csv(path: Path) -> List[VocabCard]:
    cards: List[VocabCard] = []
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = set(reader.fieldnames or [])
        for row in reader:
            if "Ukrainian" in fieldnames and "English" in fieldnames:
                ukrainian = clean_text(row.get("Ukrainian", ""))
                english = clean_text(row.get("English", ""))
                example = clean_text(row.get("Example", ""))
            elif "Front" in fieldnames and "Back" in fieldnames:
                if row.get("CardType") == "sentence":
                    continue
                ukrainian = clean_text(row.get("Front", ""))
                english, example = split_basic_back(row.get("Back", ""))
            else:
                raise ValueError(f"Unsupported CSV columns in {path}: {reader.fieldnames}")

            if not ukrainian or not english:
                continue
            cards.append(
                VocabCard(
                    ukrainian=ukrainian,
                    english=english,
                    example=example,
                    tags=row.get("Tags", "ukrainian podcast transcript vocab"),
                    source=row.get("Source", str(path)),
                    episode=row.get("Episode", path.stem),
                )
            )
    return cards


def split_basic_back(value: str) -> tuple[str, str]:
    parts = value.split("<br><br>", 1)
    english = clean_text(re.sub(r"<[^>]+>", "", parts[0]))
    example = clean_text(re.sub(r"<[^>]+>", "", parts[1])) if len(parts) > 1 else ""
    return english, example


def build_vocab_back(card: VocabCard) -> str:
    if card.example:
        return f"{card.english}<br><br>{highlight_term(card.example, card.ukrainian)}"
    return card.english


def highlight_term(sentence: str, term: str) -> str:
    if not sentence or not term:
        return sentence
    return re.sub(re.escape(term), f"<b>{term}</b>", sentence, flags=re.IGNORECASE)


def build_sentence_front(sentence: str, term: str) -> str:
    if not sentence or not term:
        return ""
    if not re.search(re.escape(term), sentence, flags=re.IGNORECASE):
        return ""
    return re.sub(re.escape(term), "[...]", sentence, count=1, flags=re.IGNORECASE)


def read_or_create_transcript(args: argparse.Namespace, episode: Optional[Episode]) -> tuple[str, str, str]:
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        episode_name = args.episode_name or transcript_path.stem
        LOGGER.info("Reading transcript file: %s", transcript_path)
        transcript = transcript_path.read_text(encoding="utf-8-sig")
        transcript = maybe_repair_mojibake(transcript, args.repair_mojibake)
        return transcript, str(transcript_path), episode_name

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
            LOGGER.info("Downloading audio for episode: %s", episode.title)
            download_file(episode.audio_url, audio_path)

    transcript_path = args.transcript_dir / safe_filename(episode_name, ".txt")
    if transcript_path.exists() and not args.force_transcribe:
        LOGGER.info("Using cached transcript: %s", transcript_path)
        transcript = transcript_path.read_text(encoding="utf-8-sig")
        transcript = maybe_repair_mojibake(transcript, args.repair_mojibake)
        return transcript, source, episode_name

    LOGGER.info("Transcribing audio with %s: %s", args.transcriber, audio_path)
    transcript = transcribe_audio(
        audio_path,
        provider=args.transcriber,
        output_dir=args.transcript_dir,
        model=args.transcription_model,
    )
    transcript = maybe_repair_mojibake(transcript, args.repair_mojibake)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(transcript, encoding="utf-8")
    LOGGER.info("Transcript written: %s (%d chars)", transcript_path, len(transcript))
    return transcript, source, episode_name


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Anki vocab cards from a podcast transcript.")
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL, help="Podcast RSS feed URL.")
    parser.add_argument("--episode-index", type=int, default=0, help="RSS episode index, newest is 0.")
    parser.add_argument("--title-search", default="", help="Pick the first RSS episode title containing this text.")
    parser.add_argument("--audio-file", type=Path, help="Use a local audio file instead of RSS download.")
    parser.add_argument("--transcript-file", type=Path, help="Use an existing transcript and skip transcription.")
    parser.add_argument("--review-input", type=Path, help="Review an existing generated CSV instead of extracting.")
    parser.add_argument("--format-input", type=Path, help="Reformat an existing generated CSV without model calls.")
    parser.add_argument("--resume-input", type=Path, help="Seed extraction from a previously written checkpoint CSV.")
    parser.add_argument(
        "--resume-after-batch",
        type=int,
        default=0,
        help="Skip this many completed transcript extraction batches when using --resume-input.",
    )
    parser.add_argument("--episode-name", default="", help="Name to store in the Episode CSV column.")
    parser.add_argument("--audio-dir", type=Path, default=Path("audio"), help="Where downloaded audio goes.")
    parser.add_argument(
        "--transcript-dir", type=Path, default=Path("transcripts"), help="Where transcripts are cached."
    )
    parser.add_argument("--output", type=Path, default=Path("anki_from_transcript.csv"))
    parser.add_argument("--max-cards", type=int, default=60)
    parser.add_argument(
        "--card-format",
        choices=["fields", "basic", "basic-with-sentences", "bidirectional-with-sentences"],
        default="fields",
        help=(
            "fields writes Ukrainian/English/Example columns; basic writes direct Front/Back rows; "
            "basic-with-sentences also adds sentence rows; bidirectional-with-sentences writes separate "
            "word and fill-the-gap sentence files for different Anki note types."
        ),
    )
    parser.add_argument(
        "--batch-chars",
        type=int,
        default=DEFAULT_BATCH_CHARS,
        help="Approximate transcript characters per vocabulary extraction batch. Use 0 to disable batching.",
    )
    parser.add_argument(
        "--cards-per-batch",
        type=int,
        default=DEFAULT_CARDS_PER_BATCH,
        help="Maximum cards to ask the model for in each batch.",
    )
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument(
        "--repair-mojibake",
        choices=["auto", "always", "never"],
        default="auto",
        help="Repair transcripts that look like UTF-8 text decoded as Windows-1252.",
    )
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
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=int(os.environ.get("OLLAMA_TIMEOUT", DEFAULT_OLLAMA_TIMEOUT)),
        help="Seconds to wait for local Ollama vocabulary generation.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=int(os.environ.get("OLLAMA_NUM_PREDICT", DEFAULT_OLLAMA_NUM_PREDICT)),
        help="Maximum response tokens generated by Ollama per extraction or review request.",
    )
    parser.add_argument(
        "--ollama-retries",
        type=int,
        default=int(os.environ.get("OLLAMA_RETRIES", DEFAULT_OLLAMA_RETRIES)),
        help="Number of retries when Ollama returns malformed JSON.",
    )
    parser.add_argument("--review-cards", action="store_true", help="Run a second LLM pass to validate/edit/drop cards.")
    parser.add_argument("--review-batch-size", type=int, default=20, help="Cards per LLM review batch.")
    parser.add_argument("--text-model", default=os.environ.get("OPENAI_TEXT_MODEL", DEFAULT_TEXT_MODEL))
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Logging verbosity.",
    )
    parser.add_argument("--log-file", type=Path, help="Optional log file path.")
    return parser


def configure_logging(level: str, log_file: Optional[Path]) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level, args.log_file)
    started = time.time()

    try:
        LOGGER.info("Starting episode-to-Anki pipeline")
        if args.resume_after_batch and not args.resume_input:
            raise RuntimeError("--resume-after-batch requires --resume-input.")
        if args.format_input:
            LOGGER.info("Reading cards for reformatting: %s", args.format_input)
            cards = read_cards_csv(args.format_input)
            count = write_cards(cards, args.output, card_format=args.card_format)
            LOGGER.info("Wrote %d reformatted rows from %s", count, args.format_input)
            return 0

        if args.review_input:
            vocab_model = args.ollama_model if args.vocab_provider == "ollama" else args.text_model
            LOGGER.info("Reading cards for review: %s", args.review_input)
            cards = read_cards_csv(args.review_input)
            LOGGER.info("Loaded %d vocabulary cards for review", len(cards))
            if args.vocab_provider != "ollama":
                raise RuntimeError("--review-input currently supports local Ollama review only.")
            cards = review_cards_ollama(
                cards,
                model=vocab_model,
                url=args.ollama_url,
                timeout=args.ollama_timeout,
                num_predict=args.ollama_num_predict,
                retries=args.ollama_retries,
                batch_size=args.review_batch_size,
                checkpoint_output=args.output,
                card_format=args.card_format,
            )
            count = write_cards(cards, args.output, card_format=args.card_format)
            LOGGER.info("Wrote %d reviewed cards to %s", count, args.output)
            LOGGER.info("Elapsed: %.1fs", time.time() - started)
            return 0

        episode = None
        if not args.audio_file and not args.transcript_file:
            LOGGER.info("Fetching RSS feed: %s", args.rss_url)
            episodes = parse_rss(fetch_text(args.rss_url))
            episode = select_episode(
                episodes,
                index=args.episode_index,
                title_search=args.title_search,
            )

        if args.transcriber == "openai" and args.transcription_model == DEFAULT_LOCAL_TRANSCRIPTION_MODEL:
            args.transcription_model = DEFAULT_TRANSCRIPTION_MODEL

        transcript, source, episode_name = read_or_create_transcript(args, episode)
        LOGGER.info("Transcript ready: %d chars", len(transcript))
        vocab_model = args.ollama_model if args.vocab_provider == "ollama" else args.text_model
        initial_cards: List[VocabCard] = []
        if args.resume_input:
            initial_cards = read_cards_csv(args.resume_input)
            LOGGER.info("Loaded %d checkpointed vocabulary items from %s", len(initial_cards), args.resume_input)
        if args.batch_chars and len(transcript) > args.batch_chars:
            cards = extract_vocab_batched(
                transcript,
                provider=args.vocab_provider,
                model=vocab_model,
                max_cards=args.max_cards,
                cards_per_batch=args.cards_per_batch,
                batch_chars=args.batch_chars,
                episode=episode_name,
                source=source,
                ollama_url=args.ollama_url,
                ollama_timeout=args.ollama_timeout,
                ollama_num_predict=args.ollama_num_predict,
                ollama_retries=args.ollama_retries,
                initial_cards=initial_cards,
                resume_after_batch=args.resume_after_batch,
                checkpoint_output=args.output,
                card_format=args.card_format,
            )
        else:
            if initial_cards or args.resume_after_batch:
                raise RuntimeError("--resume-input/--resume-after-batch require batched extraction.")
            LOGGER.info("Extracting vocabulary in a single request")
            cards = extract_vocab(
                transcript,
                provider=args.vocab_provider,
                model=vocab_model,
                max_cards=args.max_cards,
                episode=episode_name,
                source=source,
                ollama_url=args.ollama_url,
                ollama_timeout=args.ollama_timeout,
                ollama_num_predict=args.ollama_num_predict,
                ollama_retries=args.ollama_retries,
            )
        if args.review_cards:
            if args.vocab_provider != "ollama":
                raise RuntimeError("--review-cards currently supports local Ollama review only.")
            extraction_snapshot = suffixed_output_path(args.output, "extracted")
            extracted_count = write_cards(cards, extraction_snapshot, card_format="fields")
            LOGGER.info("Extraction snapshot written before review: %s (%d cards)", extraction_snapshot, extracted_count)
            cards = review_cards_ollama(
                cards,
                model=vocab_model,
                url=args.ollama_url,
                timeout=args.ollama_timeout,
                num_predict=args.ollama_num_predict,
                retries=args.ollama_retries,
                batch_size=args.review_batch_size,
                checkpoint_output=args.output,
                card_format=args.card_format,
            )
        count = write_cards(cards, args.output, card_format=args.card_format)
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1

    LOGGER.info("Wrote %d vocabulary cards to %s", count, args.output)
    LOGGER.info("Elapsed: %.1fs", time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
