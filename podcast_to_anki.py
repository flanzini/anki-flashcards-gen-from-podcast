"""Generate Anki-importable vocabulary flashcards from podcast materials.

The exporter looks for Ukrainian-English pairs in text using common formats:

    українською - English
    українською: English
    українською = English
    українською<TAB>English

Use it with copied lesson-note text, a vocabulary text/CSV file, or public RSS
episode notes. It does not invent translations.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import textwrap
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


DEFAULT_RSS_URL = "https://feeds.buzzsprout.com/1370836.rss"
USER_AGENT = "Mozilla/5.0 (compatible; podcast-to-anki/1.0)"
PAIR_RE = re.compile(
    r"^\s*(?P<front>[^:=\-–—|]{2,80}?)\s*(?:-|–|—|:|=|\|)\s*"
    r"(?P<back>.{2,160}?)\s*$"
)
TAG_CLEANUP_RE = re.compile(r"[^a-zA-Z0-9_]+")
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
SKIP_FRONTS = {
    "конспект уроку",
    "питання",
    "відповіді",
    "словничок",
    "транскрипт",
}
SKIP_BACK_PREFIXES = (
    "lesson notes",
    "comprehension questions",
    "this episode",
    "full transcript",
)


@dataclass(frozen=True)
class Episode:
    title: str
    link: str
    audio_url: str
    description_html: str
    published: str


@dataclass(frozen=True)
class AnkiCard:
    ukrainian: str
    english: str
    tags: str
    source: str
    episode: str


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_rss(xml_text: str) -> List[Episode]:
    root = ET.fromstring(xml_text)
    episodes: List[Episode] = []

    for item in root.findall("./channel/item"):
        title = child_text(item, "title")
        link = child_text(item, "link")
        description = child_text(item, "description")
        published = child_text(item, "pubDate")
        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url", "") if enclosure is not None else ""

        episodes.append(
            Episode(
                title=title,
                link=link,
                audio_url=audio_url,
                description_html=description,
                published=published,
            )
        )

    return episodes


def parse_vocab_file(path: Path, *, source: str = "") -> List[AnkiCard]:
    text = path.read_text(encoding="utf-8-sig")
    return extract_vocabulary_cards_from_lines(
        text_to_lines(text),
        episode=path.stem,
        source=source or str(path),
    )


def child_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    return "" if child is None or child.text is None else child.text.strip()


def html_to_lines(value: str) -> List[str]:
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|li|div|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return text_to_lines(value)


def text_to_lines(value: str) -> List[str]:
    return [normalize_space(line) for line in value.splitlines() if normalize_space(line)]


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_vocabulary_cards(episode: Episode) -> List[AnkiCard]:
    return extract_vocabulary_cards_from_lines(
        html_to_lines(episode.description_html),
        episode=episode.title,
        source=episode.link or episode.audio_url,
    )


def extract_vocabulary_cards_from_lines(
    lines: Iterable[str],
    *,
    episode: str = "",
    source: str = "",
) -> List[AnkiCard]:
    cards: List[AnkiCard] = []
    seen = set()

    for line in lines:
        line = line.strip(" -*\u2022\t")
        match = PAIR_RE.match(line)
        if not match:
            continue

        front = cleanup_card_text(match.group("front"))
        back = cleanup_card_text(match.group("back"))
        key = (front.casefold(), back.casefold(), episode.casefold())
        if not should_keep_vocabulary_pair(front, back) or key in seen:
            continue

        seen.add(key)
        cards.append(
            AnkiCard(
                ukrainian=front,
                english=back,
                tags=f"ukrainian podcast {episode_tag(episode)} vocab",
                source=source,
                episode=episode,
            )
        )

    return cards


def should_keep_vocabulary_pair(front: str, back: str) -> bool:
    if not front or not back:
        return False
    if front.casefold() in SKIP_FRONTS:
        return False
    if back.casefold().startswith(SKIP_BACK_PREFIXES):
        return False
    if not CYRILLIC_RE.search(front):
        return False
    if not LATIN_RE.search(back):
        return False
    return True


def cleanup_card_text(value: str) -> str:
    value = normalize_space(value)
    return value.strip(" \"'.,;:()[]{}")


def episode_tag(title: str) -> str:
    tag = TAG_CLEANUP_RE.sub("_", title.lower()).strip("_")
    return tag[:80] or "episode"


def cards_from_episodes(
    episodes: Sequence[Episode],
    *,
    limit: Optional[int] = None,
) -> List[AnkiCard]:
    selected = episodes[:limit] if limit else episodes
    cards: List[AnkiCard] = []

    for episode in selected:
        cards.extend(extract_vocabulary_cards(episode))

    return cards


def write_anki_csv(cards: Iterable[AnkiCard], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Ukrainian", "English", "Tags", "Source", "Episode"])
        for card in cards:
            writer.writerow([card.ukrainian, card.english, card.tags, card.source, card.episode])
            count += 1

    return count


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an Anki CSV from podcast RSS episode notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              python podcast_to_anki.py --vocab-file episode_notes.txt
              python podcast_to_anki.py --vocab-file episode_notes.txt --output ukrainian_vocab.csv
              python podcast_to_anki.py --rss-url https://example.com/feed.xml --limit 10
            """
        ),
    )
    parser.add_argument(
        "--vocab-file",
        type=Path,
        help="Text file containing Ukrainian-English vocabulary pairs.",
    )
    parser.add_argument("--rss-url", default=DEFAULT_RSS_URL, help="Podcast RSS feed URL.")
    parser.add_argument(
        "--output",
        default="anki_podcast_cards.csv",
        type=Path,
        help="CSV file to write for Anki import.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the newest N episodes from the RSS feed.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        if args.vocab_file:
            episodes = []
            cards = parse_vocab_file(args.vocab_file)
        else:
            xml_text = fetch_text(args.rss_url)
            episodes = parse_rss(xml_text)
            cards = cards_from_episodes(episodes, limit=args.limit)
        count = write_anki_csv(cards, args.output)
    except Exception as exc:  # pragma: no cover - command-line friendly error path
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.vocab_file:
        print(f"Wrote {count} vocabulary cards from {args.vocab_file}.")
    else:
        print(f"Wrote {count} vocabulary cards from {min(args.limit or len(episodes), len(episodes))} episodes.")
        if count == 0:
            print("No Ukrainian-English vocabulary pairs were visible in the RSS episode notes.")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
