from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .errors import AniCatError

SEASON_URL_PATTERN = re.compile(r"^https?://(?:www\.)?anime1\.me/category/[^\s]+$", re.I)
EPISODE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?anime1\.me/\d+/?(?:[?#][^\s]*)?$",
    re.I,
)
URL_SEPARATOR_PATTERN = re.compile(r"[,\s]+")


def split_urls(values: Sequence[str]) -> list[str]:
    """Split CLI URL arguments that may contain comma or whitespace separators."""

    return [item for value in values for item in URL_SEPARATOR_PATTERN.split(value.strip()) if item]


def is_season_url(url: str) -> bool:
    """Return whether a URL points to an Anime1 category/season page."""

    return bool(SEASON_URL_PATTERN.search(url))


def is_episode_url(url: str) -> bool:
    """Return whether a URL points to an Anime1 episode page."""

    return bool(EPISODE_URL_PATTERN.search(url))


def ensure_supported_url(url: str) -> None:
    """Raise when a URL is outside the supported Anime1 URL shapes."""

    if not is_season_url(url) and not is_episode_url(url):
        raise AniCatError(f"unsupported Anime1 URL: {url}")


def dedupe(values: Iterable[str]) -> list[str]:
    """Remove duplicates while preserving the original order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
