"""Push approved study-card CSV exports to local Anki through AnkiConnect."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


DEFAULT_ANKI_URL = "http://127.0.0.1:8765"
DEFAULT_WORD_MODEL = "Basic (and reversed card)"
DEFAULT_SENTENCE_MODEL = "Basic"
MANAGED_TAG = "episode_to_anki"


@dataclass(frozen=True)
class AnkiStudyNote:
    front: str
    back: str
    deck: str
    model: str
    tags: List[str]
    identity_tag: str


@dataclass(frozen=True)
class AnkiPushResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.added + self.updated + self.skipped


def anki_invoke(action: str, params: Optional[dict[str, Any]] = None, *, url: str, timeout: int = 20) -> Any:
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


def clean_tag(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", value.strip()).strip("_")
    return cleaned.lower() or "unknown"


def identity_tag(front: str, episode: str, card_type: str) -> str:
    identity = "\0".join([episode.casefold(), card_type.casefold(), front.casefold()])
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return f"episode_to_anki_id_{digest}"


def row_to_note(row: dict[str, str], *, deck: str, model: str, card_type: str) -> AnkiStudyNote:
    front = row.get("Front", "").strip()
    back = row.get("Back", "").strip()
    if not front or not back:
        raise ValueError("Approved Anki rows require non-empty Front and Back fields.")
    episode = row.get("Episode", "").strip()
    unique_tag = identity_tag(front, episode, card_type)
    tags = [tag for tag in row.get("Tags", "").split() if tag]
    tags.extend([MANAGED_TAG, unique_tag, f"card_type_{clean_tag(card_type)}"])
    if episode:
        tags.append(f"episode_{clean_tag(episode)}")
    source = row.get("Source", "").strip()
    if source:
        tags.append(f"source_{clean_tag(Path(source).stem)}")
    return AnkiStudyNote(front, back, deck, model, list(dict.fromkeys(tags)), unique_tag)


def read_approved_notes(path: Optional[Path], *, deck: str, model: str, card_type: str) -> List[AnkiStudyNote]:
    if not path:
        return []
    if not path.exists():
        raise FileNotFoundError(f"Approved export not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return [
            row_to_note(row, deck=deck, model=model, card_type=card_type)
            for row in csv.DictReader(input_file)
        ]


def note_payload(note: AnkiStudyNote) -> dict[str, Any]:
    return {
        "deckName": note.deck,
        "modelName": note.model,
        "fields": {"Front": note.front, "Back": note.back},
        "tags": note.tags,
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
    }


def push_notes(
    notes: Iterable[AnkiStudyNote],
    *,
    url: str = DEFAULT_ANKI_URL,
    update_existing: bool = False,
) -> AnkiPushResult:
    notes = list(notes)
    if not notes:
        return AnkiPushResult()
    for deck in sorted({note.deck for note in notes}):
        anki_invoke("createDeck", {"deck": deck}, url=url)
    added = updated = skipped = 0
    for note in notes:
        existing = anki_invoke("findNotes", {"query": f"tag:{note.identity_tag}"}, url=url)
        if existing:
            if not update_existing:
                skipped += 1
                continue
            note_id = existing[0]
            anki_invoke(
                "updateNoteFields",
                {"note": {"id": note_id, "fields": {"Front": note.front, "Back": note.back}}},
                url=url,
            )
            anki_invoke("addTags", {"notes": [note_id], "tags": " ".join(note.tags)}, url=url)
            updated += 1
            continue
        can_add = anki_invoke("canAddNotes", {"notes": [note_payload(note)]}, url=url)
        if not can_add or not can_add[0]:
            skipped += 1
            continue
        anki_invoke("addNote", {"note": note_payload(note)}, url=url)
        added += 1
    return AnkiPushResult(added, updated, skipped)


def push_approved_exports(
    words_path: Path,
    sentences_path: Optional[Path],
    *,
    deck: str,
    word_model: str = DEFAULT_WORD_MODEL,
    sentence_model: str = DEFAULT_SENTENCE_MODEL,
    url: str = DEFAULT_ANKI_URL,
    update_existing: bool = False,
) -> AnkiPushResult:
    notes = read_approved_notes(words_path, deck=deck, model=word_model, card_type="vocabulary")
    notes.extend(read_approved_notes(sentences_path, deck=deck, model=sentence_model, card_type="sentence"))
    return push_notes(notes, url=url, update_existing=update_existing)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Push approved word and sentence CSV exports to AnkiConnect.")
    parser.add_argument("--words", type=Path, required=True, help="Approved *_words.csv file.")
    parser.add_argument("--sentences", type=Path, help="Optional approved *_sentences.csv file.")
    parser.add_argument("--deck", required=True, help="Target Anki deck, for example Ukrainian::ULP 2-50.")
    parser.add_argument("--word-model", default=DEFAULT_WORD_MODEL)
    parser.add_argument("--sentence-model", default=DEFAULT_SENTENCE_MODEL)
    parser.add_argument("--anki-url", default=DEFAULT_ANKI_URL)
    parser.add_argument("--update-existing", action="store_true", help="Update notes previously pushed by this tool.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = push_approved_exports(
            args.words,
            args.sentences,
            deck=args.deck,
            word_model=args.word_model,
            sentence_model=args.sentence_model,
            url=args.anki_url,
            update_existing=args.update_existing,
        )
    except Exception as exc:
        print(f"Anki push failed: {exc}")
        return 1
    print(f"Anki push complete: {result.added} added, {result.updated} updated, {result.skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
