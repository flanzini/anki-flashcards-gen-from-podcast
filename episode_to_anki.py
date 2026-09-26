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
import difflib
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
UKRAINIAN_TOKEN_RE = re.compile(r"[\u0400-\u04ff'’]+")
DEFAULT_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_LOCAL_TRANSCRIPTION_MODEL = "medium"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OLLAMA_TIMEOUT = 900
DEFAULT_OLLAMA_NUM_PREDICT = 2048
DEFAULT_OLLAMA_RETRIES = 2
DEFAULT_OPENAI_TIMEOUT = 180
DEFAULT_OPENAI_RETRIES = 2
DEFAULT_BATCH_CHARS = 5000
DEFAULT_CARDS_PER_BATCH = 10
DEFAULT_REVIEW_BATCH_SIZE = 20
DEFAULT_OPENAI_REVIEW_BATCH_SIZE = 60
DEFAULT_ANKI_URL = "http://127.0.0.1:8765"

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
    example_target: str = ""


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    ukrainian: str
    message: str


@dataclass(frozen=True)
class ReviewDisposition:
    card: VocabCard
    decision: str
    reason: str
    decision_confidence: str = ""
    translation_confidence: str = ""
    normalization_confidence: str = ""


@dataclass(frozen=True)
class ReviewResult:
    accepted: List[VocabCard]
    needs_review: List[ReviewDisposition]
    rejected: List[ReviewDisposition]


@dataclass(frozen=True)
class QualityCheckResult:
    status: str
    check: str
    ukrainian: str
    message: str


@dataclass(frozen=True)
class AnkiDuplicateMatch:
    note_id: int
    deck: str
    front: str
    back: str
    similarity: float
    match_type: str


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
    needs_review_output: Optional[Path] = None,
    rejected_output: Optional[Path] = None,
    recover_needs_review: bool = False,
    card_format: str = "fields",
    initial_needs_review: Sequence[ReviewDisposition] = (),
) -> ReviewResult:
    if not cards:
        return ReviewResult([], dedupe_dispositions(initial_needs_review), [])

    accepted: List[VocabCard] = []
    needs_review: List[ReviewDisposition] = list(initial_needs_review)
    rejected: List[ReviewDisposition] = []
    batches = [list(cards[index : index + batch_size]) for index in range(0, len(cards), batch_size)]
    LOGGER.info("Reviewing %d vocabulary items in %d batch(es) using separated passes", len(cards), len(batches))

    for index, batch in enumerate(batches, start=1):
        LOGGER.info("Selection pass batch %d/%d: %d cards", index, len(batches), len(batch))
        selection_content = request_ollama_review_json(
            build_selection_prompt(batch),
            model=model,
            url=url,
            timeout=timeout,
            num_predict=num_predict,
            retries=retries,
            pass_name="selection",
        )
        selection = review_decisions_from_json(selection_content, batch, phase="selection")
        plausible_cards = selection.accepted + [item.card for item in selection.needs_review]
        rejected.extend(selection.rejected)

        batch_result = ReviewResult([], [], [])
        if plausible_cards:
            LOGGER.info("Lexical pass batch %d/%d: %d plausible cards", index, len(batches), len(plausible_cards))
            lexical_content = request_ollama_review_json(
                build_lexical_prompt(plausible_cards),
                model=model,
                url=url,
                timeout=timeout,
                num_predict=num_predict,
                retries=retries,
                pass_name="lexical",
            )
            batch_result = review_decisions_from_json(lexical_content, plausible_cards, phase="lexical")

        if recover_needs_review and batch_result.needs_review:
            pending_cards = [item.card for item in batch_result.needs_review]
            LOGGER.info("Recovery pass batch %d/%d: %d pending cards", index, len(batches), len(pending_cards))
            recovery_content = request_ollama_review_json(
                build_recovery_prompt(batch_result.needs_review),
                model=model,
                url=url,
                timeout=timeout,
                num_predict=num_predict,
                retries=retries,
                pass_name="recovery",
            )
            recovery = review_decisions_from_json(recovery_content, pending_cards, phase="lexical")
            batch_result = ReviewResult(
                accepted=batch_result.accepted + recovery.accepted,
                needs_review=recovery.needs_review,
                rejected=batch_result.rejected + recovery.rejected,
            )

        accepted.extend(batch_result.accepted)
        needs_review.extend(batch_result.needs_review)
        rejected.extend(batch_result.rejected)
        accepted = dedupe_cards(accepted)
        needs_review = dedupe_dispositions(needs_review)
        rejected = dedupe_dispositions(rejected)
        LOGGER.info(
            "Review batch %d/%d complete: %d accepted, %d pending, %d rejected so far",
            index,
            len(batches),
            len(accepted),
            len(needs_review),
            len(rejected),
        )
        if checkpoint_output:
            count = write_cards(accepted, checkpoint_output, card_format=card_format)
            LOGGER.info("Review checkpoint written: %s (%d rows)", checkpoint_output, count)
            if card_format == "bidirectional-with-sentences":
                fields_checkpoint = suffixed_output_path(checkpoint_output, "reviewed_checkpoint")
                write_cards(accepted, fields_checkpoint, card_format="fields")
                LOGGER.info("Fields review checkpoint written for resume: %s", fields_checkpoint)
        if needs_review_output:
            pending_count = write_review_dispositions(needs_review, needs_review_output)
            LOGGER.info("Needs-review audit written: %s (%d rows)", needs_review_output, pending_count)
        if rejected_output:
            rejected_count = write_review_dispositions(rejected, rejected_output)
            LOGGER.info("Rejected-card audit written: %s (%d rows)", rejected_output, rejected_count)

    return ReviewResult(dedupe_cards(accepted), dedupe_dispositions(needs_review), dedupe_dispositions(rejected))


def request_ollama_review_json(
    prompt: str,
    *,
    model: str,
    url: str,
    timeout: int,
    num_predict: int,
    retries: int,
    pass_name: str,
) -> str:
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
                f"Ollama {pass_name} review timed out after {timeout}s. "
                "Try a smaller --review-batch-size value or increase --ollama-timeout."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Ollama during {pass_name} review.") from exc

        content = data.get("response") or data.get("thinking") or ""
        try:
            parse_json_object(content)
            return content
        except json.JSONDecodeError as exc:
            if attempt < retries:
                LOGGER.warning(
                    "Ollama returned malformed %s JSON; retrying request (%d/%d)",
                    pass_name,
                    attempt + 1,
                    retries,
                )
                continue
            raise RuntimeError(f"Ollama repeatedly returned malformed JSON during {pass_name} review.") from exc
    raise RuntimeError(f"Ollama {pass_name} review did not return a result.")


def request_openai_json(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    timeout: int,
    retries: int,
    pass_name: str,
) -> str:
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    for attempt in range(retries + 1):
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
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise RuntimeError(
                f"OpenAI {pass_name} timed out after {timeout}s. "
                "Try a smaller --review-batch-size or increase --openai-timeout."
            ) from exc
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI {pass_name} failed: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach OpenAI during {pass_name}.") from exc

        try:
            content = extract_response_text(data)
            parse_json_object(content)
            return content
        except (ValueError, json.JSONDecodeError) as exc:
            if attempt < retries:
                LOGGER.warning(
                    "OpenAI returned malformed %s JSON; retrying request (%d/%d)",
                    pass_name,
                    attempt + 1,
                    retries,
                )
                continue
            raise RuntimeError(f"OpenAI repeatedly returned malformed JSON during {pass_name}.") from exc
    raise RuntimeError(f"OpenAI {pass_name} did not return a result.")


def review_cards_openai(
    cards: Sequence[VocabCard],
    *,
    model: str,
    api_key: str,
    timeout: int,
    retries: int,
    batch_size: int,
    checkpoint_output: Optional[Path] = None,
    needs_review_output: Optional[Path] = None,
    rejected_output: Optional[Path] = None,
    recover_needs_review: bool = False,
    synthesize_examples: bool = False,
    card_format: str = "fields",
    initial_needs_review: Sequence[ReviewDisposition] = (),
) -> ReviewResult:
    """Single-pass OpenAI review: triage + lexical cleanup in one call per batch."""
    if not cards:
        return ReviewResult([], dedupe_dispositions(initial_needs_review), [])

    accepted: List[VocabCard] = []
    needs_review: List[ReviewDisposition] = list(initial_needs_review)
    rejected: List[ReviewDisposition] = []
    batches = [list(cards[index : index + batch_size]) for index in range(0, len(cards), batch_size)]
    LOGGER.info(
        "Reviewing %d vocabulary items in %d OpenAI batch(es) (combined triage+lexical pass)",
        len(cards),
        len(batches),
    )

    for index, batch in enumerate(batches, start=1):
        LOGGER.info("Combined review batch %d/%d: %d cards", index, len(batches), len(batch))
        content = request_openai_json(
            api_key=api_key,
            model=model,
            system=(
                "You create high-quality Anki vocabulary decisions for English-speaking "
                "learners of Ukrainian. Return only valid JSON."
            ),
            user=build_combined_review_prompt(batch),
            timeout=timeout,
            retries=retries,
            pass_name="combined review",
        )
        batch_result = review_decisions_from_json(content, batch, phase="lexical")

        if recover_needs_review and batch_result.needs_review:
            pending_cards = [item.card for item in batch_result.needs_review]
            LOGGER.info("Recovery pass batch %d/%d: %d pending cards", index, len(batches), len(pending_cards))
            recovery_content = request_openai_json(
                api_key=api_key,
                model=model,
                system=(
                    "You reconsider uncertain Ukrainian vocabulary cards for Anki. "
                    "Return only valid JSON."
                ),
                user=build_recovery_prompt(batch_result.needs_review),
                timeout=timeout,
                retries=retries,
                pass_name="recovery",
            )
            recovery = review_decisions_from_json(recovery_content, pending_cards, phase="lexical")
            batch_result = ReviewResult(
                accepted=batch_result.accepted + recovery.accepted,
                needs_review=recovery.needs_review,
                rejected=batch_result.rejected + recovery.rejected,
            )

        accepted.extend(batch_result.accepted)
        needs_review.extend(batch_result.needs_review)
        rejected.extend(batch_result.rejected)
        accepted = dedupe_cards(accepted)
        needs_review = dedupe_dispositions(needs_review)
        rejected = dedupe_dispositions(rejected)
        LOGGER.info(
            "Review batch %d/%d complete: %d accepted, %d pending, %d rejected so far",
            index,
            len(batches),
            len(accepted),
            len(needs_review),
            len(rejected),
        )
        if checkpoint_output:
            count = write_cards(accepted, checkpoint_output, card_format=card_format)
            LOGGER.info("Review checkpoint written: %s (%d rows)", checkpoint_output, count)
            if card_format == "bidirectional-with-sentences":
                fields_checkpoint = suffixed_output_path(checkpoint_output, "reviewed_checkpoint")
                write_cards(accepted, fields_checkpoint, card_format="fields")
                LOGGER.info("Fields review checkpoint written for resume: %s", fields_checkpoint)
        if needs_review_output:
            pending_count = write_review_dispositions(needs_review, needs_review_output)
            LOGGER.info("Needs-review audit written: %s (%d rows)", needs_review_output, pending_count)
        if rejected_output:
            rejected_count = write_review_dispositions(rejected, rejected_output)
            LOGGER.info("Rejected-card audit written: %s (%d rows)", rejected_output, rejected_count)

    if synthesize_examples and needs_review:
        accepted, needs_review = synthesize_missing_examples_openai(
            accepted,
            needs_review,
            model=model,
            api_key=api_key,
            timeout=timeout,
            retries=retries,
            batch_size=min(batch_size, 20),
        )
        accepted = dedupe_cards(accepted)
        needs_review = dedupe_dispositions(needs_review)
        if checkpoint_output:
            write_cards(accepted, checkpoint_output, card_format=card_format)
            if card_format == "bidirectional-with-sentences":
                fields_checkpoint = suffixed_output_path(checkpoint_output, "reviewed_checkpoint")
                write_cards(accepted, fields_checkpoint, card_format="fields")
        if needs_review_output:
            write_review_dispositions(needs_review, needs_review_output)

    return ReviewResult(dedupe_cards(accepted), dedupe_dispositions(needs_review), dedupe_dispositions(rejected))


def run_review_cards(
    cards: Sequence[VocabCard],
    *,
    provider: str,
    model: str,
    ollama_url: str,
    ollama_timeout: int,
    ollama_num_predict: int,
    ollama_retries: int,
    openai_timeout: int,
    openai_retries: int,
    batch_size: int,
    checkpoint_output: Optional[Path] = None,
    needs_review_output: Optional[Path] = None,
    rejected_output: Optional[Path] = None,
    recover_needs_review: bool = False,
    synthesize_examples: bool = False,
    card_format: str = "fields",
    initial_needs_review: Sequence[ReviewDisposition] = (),
) -> ReviewResult:
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY before using OpenAI review.")
        return review_cards_openai(
            cards,
            model=model,
            api_key=api_key,
            timeout=openai_timeout,
            retries=openai_retries,
            batch_size=batch_size,
            checkpoint_output=checkpoint_output,
            needs_review_output=needs_review_output,
            rejected_output=rejected_output,
            recover_needs_review=recover_needs_review,
            synthesize_examples=synthesize_examples,
            card_format=card_format,
            initial_needs_review=initial_needs_review,
        )
    if provider == "ollama":
        if synthesize_examples:
            LOGGER.warning("--synthesize-examples is OpenAI-only; ignoring for Ollama review.")
        return review_cards_ollama(
            cards,
            model=model,
            url=ollama_url,
            timeout=ollama_timeout,
            num_predict=ollama_num_predict,
            retries=ollama_retries,
            batch_size=batch_size,
            checkpoint_output=checkpoint_output,
            needs_review_output=needs_review_output,
            rejected_output=rejected_output,
            recover_needs_review=recover_needs_review,
            card_format=card_format,
            initial_needs_review=initial_needs_review,
        )
    raise ValueError(f"Unknown review provider: {provider}")


def build_combined_review_prompt(cards: Sequence[VocabCard]) -> str:
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
        "You are reviewing candidate Ukrainian vocabulary cards for an English-speaking learner.\n"
        "In a single pass, triage usefulness and clean lemma/translation/example_target fields.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"d":"a","u":"слово","en":"word","ex":"Це слово.","t":"слово",'
        '"tc":"h","nc":"h","c":"h","r":"ok"}]}\n'
        "Codes: d=a accept, d=e edit, d=n needs_review, d=x reject_high_confidence; "
        "tc/nc/c=h high, m medium, l low.\n"
        "Field rules:\n"
        "- Always return concrete u/en values. On accept with no change, copy the input ukrainian/english/example.\n"
        "- Never use ellipsis placeholders such as ... in any field.\n"
        "- Use an empty string for ex or t only when clearing a weak example or when no safe sentence target exists.\n"
        "- r must be a short English explanation (for example 'useful slang term' or 'outro phrase'). "
        "Never put only a decision code (a/e/n/x) in r.\n\n"
        "Triage:\n"
        "- accept plausible useful vocabulary even if its example is missing, short, or unsuitable for a sentence card.\n"
        "- a standalone word remains a valid vocabulary-only card when its example is only that word.\n"
        "- reject_high_confidence only for clear noise: advertisements, outro/greeting material, URLs, "
        "malformed artifacts, or non-Ukrainian transcript noise.\n"
        "- reject grammar metalanguage when it is merely mentioned incidentally rather than taught.\n"
        "- never reject a plausible food, daily-routine, action, or lesson vocabulary item merely because the example is weak.\n\n"
        "Lexical cleanup:\n"
        "- use dictionary forms when a base vocabulary item is being taught rather than a specific phrase.\n"
        "- treat the incoming English gloss as unverified; correct it when context supports a better gloss.\n"
        "- use needs_review instead of accepting an uncertain translation or normalization.\n"
        "- example_target must be the exact inflected surface form in example that expresses the Ukrainian headword; "
        "otherwise return an empty string.\n"
        "- when the example is only the headword or another one-token fragment, retain the vocabulary card if sound "
        "and return empty example and example_target.\n\n"
        f"Candidate cards:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def build_selection_prompt(cards: Sequence[VocabCard]) -> str:
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
        "You are triaging candidate Ukrainian vocabulary for an English-speaking learner.\n"
        "Decide only whether each vocabulary concept is worth retaining; do not correct translations, lemmas, examples, or sentence targets in this pass.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"d":"a|n|x","c":"h|m|l","r":"..."}]}\n'
        "Codes: d=a accept, d=n needs_review, d=x reject_high_confidence; c=h high, c=m medium, c=l low. Keep r short.\n\n"
        "Selection criteria:\n"
        "- accept plausible useful vocabulary even if its example is missing, short, or unsuitable for a sentence card.\n"
        "- a standalone word remains a valid vocabulary-only card when its example is only that word or otherwise lacks enough context for a cloze sentence.\n"
        "- reject_high_confidence only for clear noise such as advertisements, outro/greeting material, URLs, malformed artifacts, or non-Ukrainian transcript noise.\n"
        "- reject grammar metalanguage when it is merely mentioned incidentally rather than actually taught or explained by the lesson context.\n"
        "- never reject a plausible food, daily-routine, action, or lesson vocabulary item merely because the example is weak.\n"
        "- use needs_review for plausible vocabulary whose usefulness is uncertain.\n"
        "- give a short English reason for needs_review or reject_high_confidence.\n\n"
        f"Candidate concepts:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def build_lexical_prompt(cards: Sequence[VocabCard]) -> str:
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
        "You are cleaning plausible Ukrainian vocabulary cards for an English-speaking learner.\n"
        "The concepts have already passed usefulness triage. Focus only on lemma normalization, concise English translation, and whether an example target is safe to propose.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"d":"a|e|n|x","u":"...","en":"...","ex":"...","t":"...",'
        '"tc":"h|m|l","nc":"h|m|l","r":"..."}]}\n'
        "Codes: d=a accept, d=e edit, d=n needs_review, d=x reject_high_confidence; tc/nc=h high, m medium, l low. Keep r short.\n\n"
        "Lexical criteria:\n"
        "- retain useful vocabulary even when no example sentence can be used; clear example and example_target instead of rejecting a card for weak context.\n"
        "- when the available example is only the headword or another one-token fragment, retain the vocabulary card if the term and gloss are sound, and return empty example and example_target fields.\n"
        "- use dictionary forms when a base vocabulary item is being taught rather than a specific phrase.\n"
        "- treat the incoming English gloss as unverified; actively check that it names the same object, action, or concept as the Ukrainian term in context.\n"
        "- correct translations or likely transcription errors only when supported by context; use needs_review instead of accepting an uncertain gloss.\n"
        "- example_target must be the exact inflected surface form in example that expresses the Ukrainian headword; otherwise return an empty string.\n"
        "- reject_high_confidence only if lexical inspection exposes clear noise or malformed non-Ukrainian material missed by selection.\n"
        "- use needs_review for any plausible card with uncertain translation or normalization.\n\n"
        f"Plausible cards:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def build_recovery_prompt(cards: Sequence[ReviewDisposition]) -> str:
    payload = [
        {
            "id": index,
            "ukrainian": item.card.ukrainian,
            "english": item.card.english,
            "example": item.card.example,
            "previous_reason": item.reason,
        }
        for index, item in enumerate(cards)
    ]
    return (
        "You are reconsidering Ukrainian vocabulary cards held for manual review after lexical cleanup.\n"
        "A weak or missing example is not a reason to reject useful vocabulary; preserve the word card and leave example_target empty.\n"
        "This includes useful standalone terms whose only available example is the term itself or another one-token fragment.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"d":"a|e|n|x","u":"...","en":"...","ex":"...","t":"...",'
        '"tc":"h|m|l","nc":"h|m|l","r":"..."}]}\n'
        "Codes: d=a accept, d=e edit, d=n needs_review, d=x reject_high_confidence; tc/nc=h high, m medium, l low. Keep r short.\n\n"
        "- accept or edit when translation and normalization are reliable.\n"
        "- keep as needs_review when the concept is useful but the correction is uncertain.\n"
        "- reject_high_confidence only for clear noise, malformed text, or non-Ukrainian material.\n\n"
        f"Pending cards:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def card_has_safe_sentence_example(card: VocabCard) -> bool:
    return bool(
        card.example
        and card.example_target
        and CYRILLIC_RE.search(card.example)
        and contains_exact_target(card.example, card.example_target)
        and example_target_matches_headword(card.ukrainian, card.example_target)
        and useful_sentence_context(card.example, card.example_target)
    )


def eligible_for_example_synthesis(item: ReviewDisposition) -> bool:
    """Needs-review cards that still lack a safe fill-the-gap sentence example."""
    return not card_has_safe_sentence_example(item.card)

def build_synthesize_examples_prompt(cards: Sequence[ReviewDisposition]) -> str:
    payload = [
        {
            "id": index,
            "ukrainian": item.card.ukrainian,
            "english": item.card.english,
            "existing_example": item.card.example,
            "previous_reason": item.reason,
        }
        for index, item in enumerate(cards)
    ]
    return (
        "You write short natural Ukrainian example sentences for Anki vocabulary cards.\n"
        "These cards were held for review mainly because a useful example was missing or too weak.\n"
        "Return only valid JSON in this exact shape:\n"
        '{"cards":[{"id":0,"ex":"Мені недостатньо часу.","t":"недостатньо","r":"ok"}]}\n'
        "Rules:\n"
        "- ex must be one natural Ukrainian sentence (or short clause) a learner might say or hear.\n"
        "- t must be the exact surface form of the headword as it appears in ex (inflection allowed).\n"
        "- Do not invent a different headword sense; keep the given English gloss.\n"
        "- Prefer everyday wording; avoid dictionary metalanguage.\n"
        "- If you cannot produce a safe sentence, return empty ex and t for that id.\n"
        "- Keep r short.\n\n"
        f"Cards:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def synthesize_missing_examples_openai(
    accepted: Sequence[VocabCard],
    needs_review: Sequence[ReviewDisposition],
    *,
    model: str,
    api_key: str,
    timeout: int,
    retries: int,
    batch_size: int,
) -> tuple[List[VocabCard], List[ReviewDisposition]]:
    """Optional OpenAI pass: invent safe examples for example-related needs_review cards."""
    pending = [item for item in needs_review if eligible_for_example_synthesis(item)]
    unchanged = [item for item in needs_review if not eligible_for_example_synthesis(item)]
    if not pending:
        return list(accepted), list(needs_review)

    LOGGER.info(
        "Synthesizing examples for %d needs-review card(s) lacking a safe sentence target",
        len(pending),
    )
    accepted_out = list(accepted)
    still_pending: List[ReviewDisposition] = list(unchanged)
    batches = [pending[index : index + batch_size] for index in range(0, len(pending), batch_size)]

    for batch_index, batch in enumerate(batches, start=1):
        LOGGER.info(
            "Example synthesis batch %d/%d: %d cards",
            batch_index,
            len(batches),
            len(batch),
        )
        content = request_openai_json(
            api_key=api_key,
            model=model,
            system=(
                "You write natural Ukrainian example sentences for Anki cards. "
                "Return only valid JSON."
            ),
            user=build_synthesize_examples_prompt(batch),
            timeout=timeout,
            retries=retries,
            pass_name="example synthesis",
        )
        parsed = parse_json_object(content)
        rows = parsed.get("cards", [])
        by_id = {index: item for index, item in enumerate(batch)}
        responded = set()

        for row in rows:
            try:
                card_id = int(row.get("id"))
                original_item = by_id[card_id]
            except (TypeError, ValueError, KeyError):
                continue
            responded.add(card_id)
            example = review_text_field(row, ("ex", "example"), original_item.card.example, allow_clear=True)
            target = review_text_field(row, ("t", "example_target"), original_item.card.example_target, allow_clear=True)
            note_bit = review_text_field(row, ("r", "reason", "note"), "", allow_clear=True)
            prior_note = clean_text(original_item.card.note)
            synth_note = "synthetic_example" + (f" ({note_bit})" if note_bit else "")
            merged_note = f"{prior_note}; {synth_note}" if prior_note else synth_note
            candidate = VocabCard(
                ukrainian=original_item.card.ukrainian,
                english=original_item.card.english,
                example=example,
                tags=original_item.card.tags,
                source=original_item.card.source,
                episode=original_item.card.episode,
                note=merged_note,
                example_target=target,
            )
            sanitized = sanitize_reviewed_cards([candidate])[0]
            if card_has_safe_sentence_example(sanitized):
                # Leave in needs_review with the filled example so a human can quickly Accept.
                still_pending.append(
                    disposition_for_card(
                        sanitized,
                        "needs_review",
                        "synthetic example ready for confirmation",
                        decision_confidence="medium",
                        translation_confidence=original_item.translation_confidence,
                        normalization_confidence=original_item.normalization_confidence,
                    )
                )
            else:
                still_pending.append(
                    disposition_for_card(
                        original_item.card,
                        "needs_review",
                        original_item.reason or "Could not synthesize a safe example sentence.",
                        decision_confidence=original_item.decision_confidence,
                        translation_confidence=original_item.translation_confidence,
                        normalization_confidence=original_item.normalization_confidence,
                    )
                )

        for index, item in enumerate(batch):
            if index not in responded:
                still_pending.append(item)

    filled = sum(1 for item in still_pending if "synthetic example ready" in item.reason.casefold())
    LOGGER.info("Example synthesis filled %d/%d eligible cards for manual confirmation", filled, len(pending))
    return accepted_out, dedupe_dispositions(still_pending)


def is_placeholder_review_value(value: str) -> bool:
    normalized = clean_text(value).casefold()
    return normalized in {"...", "…", "null", "none", "n/a", "-", "—", "."}


def review_text_field(row: dict, keys: Sequence[str], original: str, *, allow_clear: bool = False) -> str:
    """Read an edited review field, ignoring prompt placeholders and falling back to original."""
    for key in keys:
        if key not in row:
            continue
        raw_value = row.get(key)
        if raw_value is None:
            if allow_clear:
                return ""
            continue
        cleaned = clean_text(str(raw_value))
        if is_placeholder_review_value(cleaned):
            # Models often echo "..." from the schema example; treat as "unchanged".
            continue
        if cleaned == "":
            if allow_clear:
                return ""
            continue
        return cleaned
    return clean_text(original)


def review_decisions_from_json(
    content: str, original_cards: Sequence[VocabCard], *, phase: str
) -> ReviewResult:
    parsed = parse_json_object(content)
    rows = parsed.get("cards", [])
    original_by_id = {index: card for index, card in enumerate(original_cards)}
    accepted: List[VocabCard] = []
    needs_review: List[ReviewDisposition] = []
    rejected: List[ReviewDisposition] = []
    responded_ids = set()

    for row in rows:
        try:
            card_id = int(row.get("id"))
            original = original_by_id[card_id]
        except (TypeError, ValueError, KeyError):
            continue
        responded_ids.add(card_id)

        decision = normalize_review_decision(row.get("d") or row.get("decision", ""))
        reason = normalize_review_reason(
            row.get("r") or row.get("reason") or row.get("note") or "",
            decision,
        )
        decision_confidence = normalize_review_confidence(row.get("c") or row.get("decision_confidence", ""))
        translation_confidence = normalize_review_confidence(row.get("tc") or row.get("translation_confidence", ""))
        normalization_confidence = normalize_review_confidence(row.get("nc") or row.get("normalization_confidence", ""))

        if phase == "selection":
            candidate = original
        else:
            ukrainian = review_text_field(row, ("u", "ukrainian"), original.ukrainian)
            english = review_text_field(row, ("en", "english"), original.english)
            example = review_text_field(row, ("ex", "example"), original.example, allow_clear=True)
            note = review_text_field(row, ("r", "reason", "note"), original.note, allow_clear=True)
            example_target = review_text_field(row, ("t", "example_target"), original.example_target, allow_clear=True)
            used_original_fields = (
                ukrainian == clean_text(original.ukrainian) and english == clean_text(original.english)
            )
            if not ukrainian or not english or not CYRILLIC_RE.search(ukrainian):
                if CYRILLIC_RE.search(original.ukrainian) and clean_text(original.english):
                    LOGGER.warning(
                        "Falling back to original fields after unusable lexical edit for: %s",
                        original.ukrainian,
                    )
                    candidate = original
                    if decision in {"accept", "edit", "keep"}:
                        # Keep the accept/edit when the original card is still study-worthy.
                        pass
                    else:
                        needs_review.append(
                            disposition_for_card(
                                original,
                                "needs_review",
                                "Reviewer returned an unusable lexical edit; original card retained for manual review.",
                            )
                        )
                        continue
                else:
                    needs_review.append(
                        disposition_for_card(original, "needs_review", "Reviewer returned an unusable lexical edit.")
                    )
                    continue
            else:
                candidate = sanitize_reviewed_cards(
                    [
                        VocabCard(
                            ukrainian=ukrainian,
                            english=english,
                            example=example,
                            tags=f"{original.tags} reviewed",
                            source=original.source,
                            episode=original.episode,
                            note=note,
                            example_target=example_target,
                        )
                    ]
                )[0]
                if used_original_fields and decision == "edit":
                    decision = "accept"

        disposition = disposition_for_card(
            candidate,
            decision,
            reason,
            decision_confidence=decision_confidence,
            translation_confidence=translation_confidence,
            normalization_confidence=normalization_confidence,
        )

        if decision in {"accept", "edit", "keep"}:
            if phase != "selection" and "low" in {translation_confidence, normalization_confidence}:
                needs_review.append(
                    disposition_for_card(
                        candidate,
                        "needs_review",
                        "Low-confidence lexical decision requires manual review.",
                        decision_confidence=decision_confidence,
                        translation_confidence=translation_confidence,
                        normalization_confidence=normalization_confidence,
                    )
                )
            else:
                accepted.append(candidate)
            continue
        if decision == "needs_review":
            needs_review.append(disposition)
            continue
        if decision in {"reject_high_confidence", "drop"}:
            if decision == "drop" or is_clear_high_confidence_rejection(reason):
                rejected.append(disposition_for_card(candidate, "reject_high_confidence", reason, decision_confidence="high"))
            else:
                soft_reason = (
                    reason
                    if "without a clear" in reason.casefold()
                    else f"Rejection needs confirmation: {reason}"
                )
                needs_review.append(
                    disposition_for_card(
                        candidate,
                        "needs_review",
                        soft_reason,
                        decision_confidence=decision_confidence,
                        translation_confidence=translation_confidence,
                        normalization_confidence=normalization_confidence,
                    )
                )
            continue
        needs_review.append(
            disposition_for_card(
                candidate,
                "needs_review",
                f"Reviewer returned unsupported decision: {decision or '(empty)'}.",
            )
        )

    for card_id, original in original_by_id.items():
        if card_id not in responded_ids:
            needs_review.append(
                disposition_for_card(
                    original,
                    "needs_review",
                    "Reviewer omitted this input card from its response.",
                )
            )

    return ReviewResult(dedupe_cards(accepted), dedupe_dispositions(needs_review), dedupe_dispositions(rejected))


def reviewed_cards_from_json(
    content: str, original_cards: Sequence[VocabCard]
) -> tuple[List[VocabCard], List[ReviewDisposition]]:
    result = review_decisions_from_json(content, original_cards, phase="lexical")
    return result.accepted, result.rejected


def disposition_for_card(
    card: VocabCard,
    decision: str,
    reason: str,
    *,
    decision_confidence: str = "",
    translation_confidence: str = "",
    normalization_confidence: str = "",
) -> ReviewDisposition:
    return ReviewDisposition(
        card=card,
        decision=decision,
        reason=reason,
        decision_confidence=decision_confidence,
        translation_confidence=translation_confidence,
        normalization_confidence=normalization_confidence,
    )


def normalize_review_decision(value: object) -> str:
    value = clean_text(str(value)).casefold()
    return {
        "a": "accept",
        "e": "edit",
        "n": "needs_review",
        "x": "reject_high_confidence",
    }.get(value, value)


def normalize_review_confidence(value: object) -> str:
    value = clean_text(str(value)).casefold()
    return {"h": "high", "m": "medium", "l": "low"}.get(value, value)


def normalize_review_reason(reason: object, decision: str) -> str:
    """Replace empty or code-only reasons with a readable default note."""
    text = clean_text(str(reason or ""))
    code_only = text.casefold() in {
        "",
        "a",
        "e",
        "n",
        "x",
        "ok",
        "accept",
        "edit",
        "needs_review",
        "reject",
        "reject_high_confidence",
        "drop",
    }
    if text.casefold().startswith("rejection needs confirmation:"):
        remainder = clean_text(text.split(":", 1)[-1])
        if remainder.casefold() in {"", "x", "a", "e", "n", "reject", "reject_high_confidence", "drop"}:
            return "Model proposed reject without a clear noise explanation."
    if not code_only:
        return text
    defaults = {
        "accept": "Accepted.",
        "edit": "Edited during review.",
        "keep": "Kept.",
        "needs_review": "Model flagged for manual review (no detailed reason returned).",
        "reject_high_confidence": "Model proposed reject without a clear noise explanation.",
        "drop": "Model proposed drop without a clear noise explanation.",
    }
    return defaults.get(decision, "No reviewer reason supplied.")


def is_clear_high_confidence_rejection(reason: str) -> bool:
    reason = reason.casefold()
    clear_noise_terms = (
        "advert",
        "outro",
        "greeting",
        "goodbye",
        "membership",
        "social",
        "url",
        "website",
        "garbled",
        "malformed",
        "non-ukrainian",
        "transliterated",
    )
    return any(term in reason for term in clear_noise_terms)


def dedupe_dispositions(cards: Iterable[ReviewDisposition]) -> List[ReviewDisposition]:
    unique: List[ReviewDisposition] = []
    seen = set()
    for item in cards:
        key = item.card.ukrainian.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


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


def sanitize_reviewed_cards(cards: Iterable[VocabCard]) -> List[VocabCard]:
    sanitized: List[VocabCard] = []
    for card in cards:
        example = card.example
        example_target = card.example_target
        if example and not CYRILLIC_RE.search(example):
            LOGGER.warning("Removing non-Ukrainian example for reviewed card: %s", card.ukrainian)
            example = ""
            example_target = ""
        if example_target and not contains_exact_target(example, example_target):
            LOGGER.warning("Removing invalid example target for reviewed card: %s", card.ukrainian)
            example_target = ""
        if example_target and not example_target_matches_headword(card.ukrainian, example_target):
            LOGGER.warning("Removing unrelated example target for reviewed card: %s", card.ukrainian)
            example_target = ""
        if example_target and not useful_sentence_context(example, example_target):
            LOGGER.warning("Removing low-context sentence target for reviewed card: %s", card.ukrainian)
            example_target = ""
        sanitized.append(
            VocabCard(
                ukrainian=card.ukrainian,
                english=card.english,
                example=example,
                tags=card.tags,
                source=card.source,
                episode=card.episode,
                note=card.note,
                example_target=example_target,
            )
        )
    return sanitized


def validate_cards(cards: Iterable[VocabCard]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for card in cards:
        if card.example and not CYRILLIC_RE.search(card.example):
            issues.append(
                ValidationIssue("error", "non_ukrainian_example", card.ukrainian, "Example contains no Ukrainian text.")
            )
        if card.example_target and not contains_exact_target(card.example, card.example_target):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_example_target",
                    card.ukrainian,
                    f"Example target {card.example_target!r} is not an exact term in the example.",
                )
            )
        elif card.example_target and not example_target_matches_headword(card.ukrainian, card.example_target):
            issues.append(
                ValidationIssue(
                    "error",
                    "unrelated_example_target",
                    card.ukrainian,
                    f"Example target {card.example_target!r} is not plausibly a surface form of the headword.",
                )
            )
        elif card.example_target and not useful_sentence_context(card.example, card.example_target):
            issues.append(
                ValidationIssue(
                    "warning",
                    "weak_sentence_context",
                    card.ukrainian,
                    "Hiding the target leaves too little sentence context for recall.",
                )
            )
        if not card.example:
            issues.append(ValidationIssue("info", "missing_example", card.ukrainian, "Card has no Ukrainian example."))
        elif not card.example_target:
            issues.append(
                ValidationIssue(
                    "info",
                    "no_sentence_target",
                    card.ukrainian,
                    "Card is valid as vocabulary only; no safe sentence target was provided.",
                )
            )
    return issues


def write_validation_report(cards: Iterable[VocabCard], output_path: Path) -> int:
    issues = validate_cards(cards)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Severity", "Code", "Ukrainian", "Message"])
        for issue in issues:
            writer.writerow([issue.severity, issue.code, issue.ukrainian, issue.message])
    return len(issues)


def write_review_dispositions(cards: Iterable[ReviewDisposition], output_path: Path) -> int:
    cards = list(cards)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                "Ukrainian",
                "English",
                "Example",
                "ExampleTarget",
                "Decision",
                "Reason",
                "DecisionConfidence",
                "TranslationConfidence",
                "NormalizationConfidence",
                "Tags",
                "Source",
                "Episode",
            ]
        )
        for item in cards:
            card = item.card
            writer.writerow(
                [
                    card.ukrainian,
                    card.english,
                    card.example,
                    card.example_target,
                    item.decision,
                    item.reason,
                    item.decision_confidence,
                    item.translation_confidence,
                    item.normalization_confidence,
                    card.tags,
                    card.source,
                    card.episode,
                ]
            )
    return len(cards)


def write_rejected_cards(cards: Iterable[ReviewDisposition], output_path: Path) -> int:
    return write_review_dispositions(cards, output_path)


def anki_invoke(action: str, params: Optional[dict] = None, *, url: str, timeout: int = 30) -> object:
    payload = json.dumps({"action": action, "version": 6, "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Could not reach AnkiConnect at {url}. Ensure Anki is open and the AnkiConnect add-on is installed."
        ) from exc
    if not isinstance(data, dict) or "error" not in data or "result" not in data:
        raise RuntimeError("AnkiConnect returned an unexpected response.")
    if data["error"]:
        raise RuntimeError(f"AnkiConnect error during {action}: {data['error']}")
    return data["result"]


def normalized_ukrainian_key(text: str) -> str:
    tokens = ukrainian_tokens(text)
    return " ".join(tokens)


def fetch_anki_front_index(*, deck: str, url: str) -> List[AnkiDuplicateMatch]:
    note_ids = anki_invoke("findNotes", {"query": f'deck:"{deck}"'}, url=url)
    if not isinstance(note_ids, list) or not note_ids:
        return []
    notes = anki_invoke("notesInfo", {"notes": note_ids}, url=url)
    if not isinstance(notes, list):
        return []
    indexed: List[AnkiDuplicateMatch] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        fields = note.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        front_data = fields.get("Front")
        back_data = fields.get("Back")
        front = ""
        back = ""
        if isinstance(front_data, dict):
            front = clean_text(str(front_data.get("value", "")))
        if isinstance(back_data, dict):
            back = clean_text(str(back_data.get("value", "")))
        if not front:
            continue
        note_id = note.get("noteId")
        if not isinstance(note_id, int):
            continue
        indexed.append(
            AnkiDuplicateMatch(
                note_id=note_id,
                deck=clean_text(str(note.get("deckName", deck))) or deck,
                front=front,
                back=back,
                similarity=1.0,
                match_type="indexed",
            )
        )
    return indexed


def find_duplicate_match(
    card: VocabCard,
    anki_notes: Sequence[AnkiDuplicateMatch],
    *,
    fuzzy_threshold: float,
) -> Optional[AnkiDuplicateMatch]:
    card_key = normalized_ukrainian_key(card.ukrainian)
    if not card_key:
        return None

    exact_match: Optional[AnkiDuplicateMatch] = None
    best_fuzzy: Optional[AnkiDuplicateMatch] = None
    best_score = 0.0

    for note in anki_notes:
        note_key = normalized_ukrainian_key(note.front)
        if not note_key:
            continue
        if note_key == card_key:
            exact_match = AnkiDuplicateMatch(
                note_id=note.note_id,
                deck=note.deck,
                front=note.front,
                back=note.back,
                similarity=1.0,
                match_type="exact_front",
            )
            break
        score = difflib.SequenceMatcher(a=card_key, b=note_key).ratio()
        if score > best_score:
            best_score = score
            best_fuzzy = AnkiDuplicateMatch(
                note_id=note.note_id,
                deck=note.deck,
                front=note.front,
                back=note.back,
                similarity=score,
                match_type="fuzzy_front",
            )

    if exact_match:
        return exact_match
    if best_fuzzy and best_score >= fuzzy_threshold:
        return best_fuzzy
    return None


def mark_anki_duplicates(
    cards: Sequence[VocabCard],
    *,
    deck: str,
    url: str,
    fuzzy_threshold: float,
    duplicate_policy: str,
) -> tuple[List[VocabCard], List[ReviewDisposition]]:
    anki_notes = fetch_anki_front_index(deck=deck, url=url)
    LOGGER.info("Loaded %d existing Anki notes from deck %s for duplicate cross-check", len(anki_notes), deck)
    retained: List[VocabCard] = []
    duplicate_dispositions: List[ReviewDisposition] = []
    for card in cards:
        match = find_duplicate_match(card, anki_notes, fuzzy_threshold=fuzzy_threshold)
        if not match:
            retained.append(card)
            continue
        reason = (
            "duplicate_card: "
            f"{match.match_type} score={match.similarity:.3f} "
            f"matches note_id={match.note_id} front={match.front!r} back={match.back!r} deck={match.deck!r}"
        )
        if duplicate_policy == "keep":
            duplicate_dispositions.append(
                disposition_for_card(card, "duplicate_keep", reason, decision_confidence="medium")
            )
            retained.append(card)
            continue
        if duplicate_policy == "skip":
            duplicate_dispositions.append(
                disposition_for_card(card, "duplicate_skip", reason, decision_confidence="high")
            )
            continue
        duplicate_dispositions.append(
            disposition_for_card(
                card,
                "needs_review",
                reason,
                decision_confidence="high" if match.match_type == "exact_front" else "medium",
            )
        )
    return retained, dedupe_dispositions(duplicate_dispositions)


def evaluate_quality_fixture(cards: Iterable[VocabCard], fixture_path: Path) -> List[QualityCheckResult]:
    with fixture_path.open("r", encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)

    cards_by_term = {card.ukrainian.casefold(): card for card in sanitize_reviewed_cards(cards)}
    results: List[QualityCheckResult] = []
    for expectation in fixture.get("checks", []):
        check = str(expectation.get("check", ""))
        ukrainian = clean_text(str(expectation.get("ukrainian", "")))
        card = cards_by_term.get(ukrainian.casefold())
        passed = False
        message = ""

        if check == "keep":
            passed = card is not None
            message = "Card retained." if passed else "Required card was rejected or omitted."
        elif check == "exclude":
            passed = card is None
            message = "Card excluded." if passed else "Card should be dropped or repaired before import."
        elif check == "english_contains_any":
            values = [str(value).casefold() for value in expectation.get("values", [])]
            passed = card is not None and any(value in card.english.casefold() for value in values)
            message = (
                "Translation accepted."
                if passed
                else f"Expected translation containing one of: {', '.join(expectation.get('values', []))}."
            )
        elif check == "target_not_equals":
            values = [str(value).casefold() for value in expectation.get("values", [])]
            passed = card is None or card.example_target.casefold() not in values
            message = (
                "Unsafe sentence target absent."
                if passed
                else f"Sentence target must not be one of: {', '.join(expectation.get('values', []))}."
            )
        elif check == "target_equals_any":
            values = [str(value).casefold() for value in expectation.get("values", [])]
            passed = card is not None and card.example_target.casefold() in values
            message = (
                "Sentence target accepted."
                if passed
                else f"Expected full sentence target matching one of: {', '.join(expectation.get('values', []))}."
            )
        else:
            message = f"Unknown fixture check type: {check}."

        results.append(QualityCheckResult("pass" if passed else "fail", check, ukrainian, message))
    return results


def write_quality_report(cards: Iterable[VocabCard], fixture_path: Path, output_path: Path) -> tuple[int, int]:
    results = evaluate_quality_fixture(cards, fixture_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Status", "Check", "Ukrainian", "Message"])
        for result in results:
            writer.writerow([result.status, result.check, result.ukrainian, result.message])
    failures = sum(1 for result in results if result.status == "fail")
    return len(results), failures


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
        example_target = clean_text(str(row.get("example_target", "")))
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
                example_target=example_target,
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
        '{"cards":[{"ukrainian":"...","english":"...","example":"...","example_target":"...","note":"..."}]}\n\n'
        "Rules:\n"
        "- Extract only useful Ukrainian vocabulary for an English-speaking learner.\n"
        "- ukrainian: a normal Ukrainian dictionary form, common phrase, or lesson phrase.\n"
        "- english: concise English meaning or translation.\n"
        "- example: a short natural Ukrainian example phrase from the transcript when possible.\n"
        "- example_target: the exact Ukrainian surface word or phrase in example that expresses ukrainian; use an empty string if no useful example is available.\n"
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
        writer.writerow(["Ukrainian", "English", "Example", "ExampleTarget", "Tags", "Source", "Episode"])
        for card in cards:
            writer.writerow(
                [
                    card.ukrainian,
                    card.english,
                    card.example,
                    card.example_target,
                    card.tags,
                    card.source,
                    card.episode,
                ]
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

            sentence_front = build_sentence_front(card.example, card.example_target, card.ukrainian)
            if include_sentence_cards and sentence_front:
                writer.writerow(
                    [
                        sentence_front,
                        f"{highlight_term(card.example, card.example_target)}<br><br>{card.ukrainian} - {card.english}",
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
            sentence_front = build_sentence_front(card.example, card.example_target, card.ukrainian)
            if not sentence_front:
                continue
            writer.writerow(
                [
                    sentence_front,
                    f"{highlight_term(card.example, card.example_target)}<br><br>{card.ukrainian} - {card.english}",
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
                example_target = clean_text(row.get("ExampleTarget", ""))
            elif "Front" in fieldnames and "Back" in fieldnames:
                if row.get("CardType") == "sentence":
                    continue
                ukrainian = clean_text(row.get("Front", ""))
                english, example = split_basic_back(row.get("Back", ""))
                example_target = ""
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
                    example_target=example_target,
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
        target = (
            card.example_target
            if contains_exact_target(card.example, card.example_target)
            and example_target_matches_headword(card.ukrainian, card.example_target)
            else ""
        )
        return f"{card.english}<br><br>{highlight_term(card.example, target)}"
    return card.english


def highlight_term(sentence: str, target: str) -> str:
    if not sentence or not target:
        return sentence
    return target_pattern(target).sub(f"<b>{target}</b>", sentence, count=1)


def build_sentence_front(sentence: str, target: str, headword: str = "") -> str:
    if (
        not contains_exact_target(sentence, target)
        or (headword and not example_target_matches_headword(headword, target))
        or not useful_sentence_context(sentence, target)
    ):
        return ""
    return target_pattern(target).sub("[...]", sentence, count=1)


def contains_exact_target(sentence: str, target: str) -> bool:
    if not sentence or not target:
        return False
    return target_pattern(target).search(sentence) is not None


def useful_sentence_context(sentence: str, target: str) -> bool:
    if not contains_exact_target(sentence, target):
        return False
    prompt = target_pattern(target).sub("", sentence, count=1)
    return len(re.findall(r"[\u0400-\u04ff]{2,}", prompt)) >= 2


def example_target_matches_headword(headword: str, target: str) -> bool:
    headword_tokens = ukrainian_tokens(headword)
    target_tokens = ukrainian_tokens(target)
    if not headword_tokens or len(headword_tokens) != len(target_tokens):
        return False
    return all(plausibly_related_surface_forms(base, surface) for base, surface in zip(headword_tokens, target_tokens))


def ukrainian_tokens(text: str) -> List[str]:
    return [token.casefold().replace("’", "'") for token in UKRAINIAN_TOKEN_RE.findall(text)]


def plausibly_related_surface_forms(headword_token: str, target_token: str) -> bool:
    if headword_token == target_token:
        return True
    prefix_length = 0
    for headword_char, target_char in zip(headword_token, target_token):
        if headword_char != target_char:
            break
        prefix_length += 1
    return prefix_length >= min(3, len(headword_token), len(target_token))


def target_pattern(target: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![\u0400-\u04ff]){re.escape(target)}(?![\u0400-\u04ff])",
        flags=re.IGNORECASE,
    )


def read_or_create_transcript(
    args: argparse.Namespace, episode: Optional[Episode]
) -> tuple[str, str, str, Path]:
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        episode_name = args.episode_name or transcript_path.stem
        LOGGER.info("Reading transcript file: %s", transcript_path)
        transcript = transcript_path.read_text(encoding="utf-8-sig")
        transcript = maybe_repair_mojibake(transcript, args.repair_mojibake)
        return transcript, str(transcript_path), episode_name, transcript_path

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
        return transcript, source, episode_name, transcript_path

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
    return transcript, source, episode_name, transcript_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Anki vocab cards from a podcast transcript.")
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL, help="Podcast RSS feed URL.")
    parser.add_argument("--episode-index", type=int, default=0, help="RSS episode index, newest is 0.")
    parser.add_argument("--title-search", default="", help="Pick the first RSS episode title containing this text.")
    parser.add_argument("--audio-file", type=Path, help="Use a local audio file instead of RSS download.")
    parser.add_argument("--transcript-file", type=Path, help="Use an existing transcript and skip transcription.")
    parser.add_argument("--review-input", type=Path, help="Review an existing generated CSV instead of extracting.")
    parser.add_argument("--format-input", type=Path, help="Reformat an existing generated CSV without model calls.")
    parser.add_argument("--validate-input", type=Path, help="Write a validation report for an existing card CSV.")
    parser.add_argument("--validation-output", type=Path, default=Path("outputs/card_validation_report.csv"))
    parser.add_argument("--evaluate-input", type=Path, help="Evaluate an existing reviewed CSV against a quality fixture.")
    parser.add_argument("--quality-fixture", type=Path, help="JSON quality fixture used with --evaluate-input.")
    parser.add_argument("--quality-output", type=Path, default=Path("outputs/card_quality_report.csv"))
    parser.add_argument(
        "--rejected-output",
        type=Path,
        help="Rejected-card audit CSV path. Review runs default to an output-derived _rejected CSV.",
    )
    parser.add_argument(
        "--needs-review-output",
        type=Path,
        help="Pending-card audit CSV path. Review runs default to an output-derived _needs_review CSV.",
    )
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
    parser.add_argument("--output", type=Path, default=Path("outputs/anki_from_transcript.csv"))
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
        "--transcribe-only",
        action="store_true",
        help="Download and transcribe audio only; skip vocabulary extraction and review.",
    )
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
    parser.add_argument(
        "--review-cards",
        action="store_true",
        help="Run LLM review before deterministic validation (Ollama multi-pass or OpenAI combined pass).",
    )
    parser.add_argument(
        "--review-provider",
        choices=["ollama", "openai"],
        default=None,
        help=(
            "Provider for --review-cards / --review-input. Defaults to --vocab-provider when reviewing. "
            "OpenAI uses a single combined triage+lexical pass with larger batches."
        ),
    )
    parser.add_argument(
        "--review-batch-size",
        type=int,
        default=None,
        help=(
            f"Cards per LLM review batch. Defaults to {DEFAULT_REVIEW_BATCH_SIZE} for Ollama "
            f"and {DEFAULT_OPENAI_REVIEW_BATCH_SIZE} for OpenAI."
        ),
    )
    parser.add_argument(
        "--recover-needs-review",
        action="store_true",
        help="Run a focused additional LLM pass on cards held for manual review during lexical cleanup.",
    )
    parser.add_argument(
        "--synthesize-examples",
        action="store_true",
        help=(
            "OpenAI-only: after review, invent short natural Ukrainian example sentences for "
            "needs-review cards that lack a safe sentence target. Filled examples stay in "
            "needs-review for a quick human accept."
        ),
    )
    parser.add_argument(
        "--openai-timeout",
        type=int,
        default=int(os.environ.get("OPENAI_TIMEOUT", DEFAULT_OPENAI_TIMEOUT)),
        help="Seconds to wait for each OpenAI extraction or review request.",
    )
    parser.add_argument(
        "--openai-retries",
        type=int,
        default=int(os.environ.get("OPENAI_RETRIES", DEFAULT_OPENAI_RETRIES)),
        help="Number of retries when OpenAI returns malformed JSON.",
    )
    parser.add_argument(
        "--crosscheck-anki",
        action="store_true",
        help="Cross-check generated cards against existing Anki notes before review and route matches to needs-review.",
    )
    parser.add_argument(
        "--crosscheck-anki-deck",
        default="",
        help="Anki deck name used for duplicate cross-check. Required when --crosscheck-anki is set.",
    )
    parser.add_argument(
        "--crosscheck-anki-url",
        default=DEFAULT_ANKI_URL,
        help="AnkiConnect URL used for duplicate cross-check.",
    )
    parser.add_argument(
        "--crosscheck-fuzzy-threshold",
        type=float,
        default=0.84,
        help="Similarity threshold for fuzzy duplicate matching against Anki fronts (0-1).",
    )
    parser.add_argument(
        "--duplicates-output",
        type=Path,
        help="Optional duplicate-match audit CSV path. Defaults to output-derived _duplicates CSV when cross-check runs.",
    )
    parser.add_argument(
        "--duplicate-policy",
        choices=["needs_review", "skip", "keep"],
        default="needs_review",
        help=(
            "How to handle matched Anki duplicates during cross-check. "
            "With OpenAI review + --crosscheck-anki, defaults to skip unless this flag is set explicitly."
        ),
    )
    parser.add_argument("--text-model", default=os.environ.get("OPENAI_TEXT_MODEL", DEFAULT_TEXT_MODEL))
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Logging verbosity.",
    )
    parser.add_argument("--log-file", type=Path, help="Optional log file path.")
    return parser


def resolve_review_provider(args: argparse.Namespace) -> str:
    if args.review_provider:
        return args.review_provider
    return args.vocab_provider


def resolve_review_batch_size(args: argparse.Namespace, review_provider: str) -> int:
    if args.review_batch_size is not None:
        return args.review_batch_size
    if review_provider == "openai":
        return DEFAULT_OPENAI_REVIEW_BATCH_SIZE
    return DEFAULT_REVIEW_BATCH_SIZE


def resolve_duplicate_policy(args: argparse.Namespace, review_provider: str, argv: Optional[Sequence[str]]) -> str:
    argv_list = list(argv if argv is not None else sys.argv[1:])
    if (
        review_provider == "openai"
        and args.crosscheck_anki
        and "--duplicate-policy" not in argv_list
    ):
        return "skip"
    return args.duplicate_policy


def review_model_for_provider(args: argparse.Namespace, review_provider: str) -> str:
    if review_provider == "openai":
        return args.text_model
    return args.ollama_model


def configure_logging(level: str, log_file: Optional[Path]) -> None:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
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
    review_provider = resolve_review_provider(args)
    review_batch_size = resolve_review_batch_size(args, review_provider)
    duplicate_policy = resolve_duplicate_policy(args, review_provider, argv)
    review_model = review_model_for_provider(args, review_provider)

    try:
        LOGGER.info("Starting episode-to-Anki pipeline")
        if args.resume_after_batch and not args.resume_input:
            raise RuntimeError("--resume-after-batch requires --resume-input.")
        if args.quality_fixture and not args.evaluate_input:
            raise RuntimeError("--quality-fixture requires --evaluate-input.")
        if args.crosscheck_anki:
            if not args.crosscheck_anki_deck:
                raise RuntimeError("--crosscheck-anki requires --crosscheck-anki-deck.")
            if not (0.0 <= args.crosscheck_fuzzy_threshold <= 1.0):
                raise RuntimeError("--crosscheck-fuzzy-threshold must be between 0 and 1.")
            if not (args.review_input or args.review_cards):
                raise RuntimeError("--crosscheck-anki currently supports review flows (--review-input or --review-cards).")
            if duplicate_policy != args.duplicate_policy:
                LOGGER.info(
                    "OpenAI review with Anki cross-check: defaulting --duplicate-policy to %s",
                    duplicate_policy,
                )
        if args.transcribe_only:
            if args.transcript_file:
                raise RuntimeError(
                    "--transcribe-only downloads or transcribes audio; use RSS selection or --audio-file instead of --transcript-file."
                )
            for flag_name, flag_label in (
                ("format_input", "--format-input"),
                ("validate_input", "--validate-input"),
                ("evaluate_input", "--evaluate-input"),
                ("review_input", "--review-input"),
                ("resume_input", "--resume-input"),
            ):
                if getattr(args, flag_name):
                    raise RuntimeError(f"--transcribe-only cannot be used with {flag_label}.")
            if args.review_cards:
                raise RuntimeError("--transcribe-only cannot be used with --review-cards.")
        if args.format_input:
            LOGGER.info("Reading cards for reformatting: %s", args.format_input)
            cards = read_cards_csv(args.format_input)
            count = write_cards(cards, args.output, card_format=args.card_format)
            LOGGER.info("Wrote %d reformatted rows from %s", count, args.format_input)
            return 0
        if args.validate_input:
            LOGGER.info("Reading cards for validation: %s", args.validate_input)
            cards = read_cards_csv(args.validate_input)
            count = write_validation_report(cards, args.validation_output)
            LOGGER.info("Wrote %d validation findings to %s", count, args.validation_output)
            return 0
        if args.evaluate_input:
            if not args.quality_fixture:
                raise RuntimeError("--evaluate-input requires --quality-fixture.")
            LOGGER.info("Reading reviewed cards for quality evaluation: %s", args.evaluate_input)
            cards = read_cards_csv(args.evaluate_input)
            count, failures = write_quality_report(cards, args.quality_fixture, args.quality_output)
            LOGGER.info("Wrote %d quality checks to %s (%d failed)", count, args.quality_output, failures)
            return 1 if failures else 0

        if args.review_input:
            LOGGER.info("Reading cards for review: %s", args.review_input)
            cards = read_cards_csv(args.review_input)
            LOGGER.info("Loaded %d vocabulary cards for review via %s", len(cards), review_provider)
            rejected_output = args.rejected_output or suffixed_output_path(args.output, "rejected")
            needs_review_output = args.needs_review_output or suffixed_output_path(args.output, "needs_review")
            duplicate_dispositions: List[ReviewDisposition] = []
            duplicates_output: Optional[Path] = None
            if args.crosscheck_anki:
                duplicates_output = args.duplicates_output or suffixed_output_path(args.output, "duplicates")
                cards, duplicate_dispositions = mark_anki_duplicates(
                    cards,
                    deck=args.crosscheck_anki_deck,
                    url=args.crosscheck_anki_url,
                    fuzzy_threshold=args.crosscheck_fuzzy_threshold,
                    duplicate_policy=duplicate_policy,
                )
                duplicate_count = write_review_dispositions(duplicate_dispositions, duplicates_output)
                LOGGER.info("Duplicate-card audit written: %s (%d rows)", duplicates_output, duplicate_count)
                LOGGER.info("Cross-check retained %d cards for model review", len(cards))
            review_result = run_review_cards(
                cards,
                provider=review_provider,
                model=review_model,
                ollama_url=args.ollama_url,
                ollama_timeout=args.ollama_timeout,
                ollama_num_predict=args.ollama_num_predict,
                ollama_retries=args.ollama_retries,
                openai_timeout=args.openai_timeout,
                openai_retries=args.openai_retries,
                batch_size=review_batch_size,
                checkpoint_output=args.output,
                needs_review_output=needs_review_output,
                rejected_output=rejected_output,
                recover_needs_review=args.recover_needs_review,
                synthesize_examples=args.synthesize_examples,
                card_format=args.card_format,
                initial_needs_review=duplicate_dispositions,
            )
            cards = review_result.accepted
            count = write_cards(cards, args.output, card_format=args.card_format)
            validation_output = suffixed_output_path(args.output, "validation")
            validation_count = write_validation_report(cards, validation_output)
            LOGGER.info("Wrote %d reviewed cards to %s", count, args.output)
            LOGGER.info("Wrote %d validation findings to %s", validation_count, validation_output)
            LOGGER.info("Needs-review audit available at %s", needs_review_output)
            LOGGER.info("Rejected-card audit available at %s", rejected_output)
            if duplicates_output:
                LOGGER.info("Duplicate-card audit available at %s", duplicates_output)
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

        transcript, source, episode_name, transcript_path = read_or_create_transcript(args, episode)
        LOGGER.info("Transcript ready: %d chars", len(transcript))
        if args.transcribe_only:
            LOGGER.info("Transcribe-only mode; skipping vocabulary extraction.")
            LOGGER.info("Transcript available at %s", transcript_path)
            LOGGER.info("Elapsed: %.1fs", time.time() - started)
            return 0
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
            extraction_snapshot = suffixed_output_path(args.output, "extracted")
            extracted_count = write_cards(cards, extraction_snapshot, card_format="fields")
            LOGGER.info("Extraction snapshot written before review: %s (%d cards)", extraction_snapshot, extracted_count)
            rejected_output = args.rejected_output or suffixed_output_path(args.output, "rejected")
            needs_review_output = args.needs_review_output or suffixed_output_path(args.output, "needs_review")
            duplicate_dispositions = []
            duplicates_output: Optional[Path] = None
            if args.crosscheck_anki:
                duplicates_output = args.duplicates_output or suffixed_output_path(args.output, "duplicates")
                cards, duplicate_dispositions = mark_anki_duplicates(
                    cards,
                    deck=args.crosscheck_anki_deck,
                    url=args.crosscheck_anki_url,
                    fuzzy_threshold=args.crosscheck_fuzzy_threshold,
                    duplicate_policy=duplicate_policy,
                )
                duplicate_count = write_review_dispositions(duplicate_dispositions, duplicates_output)
                LOGGER.info("Duplicate-card audit written: %s (%d rows)", duplicates_output, duplicate_count)
                LOGGER.info("Cross-check retained %d cards for model review", len(cards))
            review_result = run_review_cards(
                cards,
                provider=review_provider,
                model=review_model,
                ollama_url=args.ollama_url,
                ollama_timeout=args.ollama_timeout,
                ollama_num_predict=args.ollama_num_predict,
                ollama_retries=args.ollama_retries,
                openai_timeout=args.openai_timeout,
                openai_retries=args.openai_retries,
                batch_size=review_batch_size,
                checkpoint_output=args.output,
                needs_review_output=needs_review_output,
                rejected_output=rejected_output,
                recover_needs_review=args.recover_needs_review,
                synthesize_examples=args.synthesize_examples,
                card_format=args.card_format,
                initial_needs_review=duplicate_dispositions,
            )
            cards = review_result.accepted
        count = write_cards(cards, args.output, card_format=args.card_format)
        if args.review_cards:
            validation_output = suffixed_output_path(args.output, "validation")
            validation_count = write_validation_report(cards, validation_output)
            LOGGER.info("Wrote %d validation findings to %s", validation_count, validation_output)
            LOGGER.info("Needs-review audit available at %s", needs_review_output)
            LOGGER.info("Rejected-card audit available at %s", rejected_output)
            if args.crosscheck_anki:
                duplicates_output = args.duplicates_output or suffixed_output_path(args.output, "duplicates")
                LOGGER.info("Duplicate-card audit available at %s", duplicates_output)
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1

    LOGGER.info("Wrote %d vocabulary cards to %s", count, args.output)
    LOGGER.info("Elapsed: %.1fs", time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
