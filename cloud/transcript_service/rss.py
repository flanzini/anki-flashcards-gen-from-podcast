from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

# Allow importing sibling repo modules when running from cloud/ or repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from podcast_to_anki import DEFAULT_RSS_URL, Episode, fetch_text, parse_rss  # noqa: E402


@dataclass(frozen=True)
class SelectedEpisode:
    title: str
    link: str
    audio_url: str
    published: str
    episode_key: str


def safe_episode_key(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")
    return (cleaned[:90] or "episode").lower()


def select_episode(
    episodes: Sequence[Episode],
    *,
    index: Optional[int] = 0,
    title_search: str = "",
) -> Episode:
    if title_search:
        lowered = title_search.casefold()
        matches = [episode for episode in episodes if lowered in episode.title.casefold()]
        if not matches:
            raise ValueError(f"No RSS episode title matched {title_search!r}.")
        return matches[0]

    if index is None:
        index = 0
    if index < 0 or index >= len(episodes):
        raise IndexError(f"Episode index {index} is out of range. Feed has {len(episodes)} episodes.")
    return episodes[index]


def load_episodes(rss_url: str = DEFAULT_RSS_URL) -> List[Episode]:
    return parse_rss(fetch_text(rss_url))


def resolve_episode(
    *,
    rss_url: str,
    episode_index: Optional[int] = 0,
    title_search: str = "",
) -> SelectedEpisode:
    episode = select_episode(
        load_episodes(rss_url),
        index=episode_index,
        title_search=title_search,
    )
    if not episode.audio_url:
        raise ValueError(f"RSS episode has no audio enclosure: {episode.title}")
    return SelectedEpisode(
        title=episode.title,
        link=episode.link,
        audio_url=episode.audio_url,
        published=episode.published,
        episode_key=safe_episode_key(episode.title),
    )
