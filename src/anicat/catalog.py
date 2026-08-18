from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any, Protocol

from .constants import ANIME_LIST_URL
from .errors import ParseError
from .extractor import parse_html
from .models import AnimeEntry
from .urls import ANIME1_ME_HOST

CATALOG_COLUMN_COUNT = 6
LOGGER = logging.getLogger(__name__)


class CatalogSource(Protocol):
    """Minimal HTTP dependency required to read the Anime1 catalogue."""

    def get_page(self, url: str) -> str:
        """Return the raw body of an Anime1 URL fetched with GET."""

        ...


def fetch_catalog(client: CatalogSource) -> list[AnimeEntry]:
    """Fetch and parse the full Anime1 catalogue index."""

    entries = parse_catalog(client.get_page(ANIME_LIST_URL))
    LOGGER.info("Loaded %d catalogue entries", len(entries))
    return entries


def parse_catalog(payload: str) -> list[AnimeEntry]:
    """Parse an animelist.json payload into catalogue entries."""

    try:
        rows = json.loads(payload)
    except ValueError as error:
        raise ParseError("catalogue response is not JSON") from error

    if not isinstance(rows, list):
        raise ParseError(f"catalogue payload is {type(rows).__name__}, expected a list")

    entries = [entry for row in rows if (entry := parse_entry(row)) is not None]
    if rows and not entries:
        raise ParseError("catalogue payload contained no usable rows")
    return entries


def parse_entry(row: Any) -> AnimeEntry | None:
    """Convert one catalogue row, or return None when its shape is unusable."""

    if not isinstance(row, list) or len(row) < CATALOG_COLUMN_COUNT:
        LOGGER.warning("Skipping malformed catalogue row: %r", row)
        return None

    anime_id, title, episodes, year, season, subtitle_group = row[:CATALOG_COLUMN_COUNT]
    if not isinstance(anime_id, int):
        LOGGER.warning("Skipping catalogue row with a non-numeric ID: %r", row)
        return None

    if anime_id:
        return AnimeEntry(
            anime_id=anime_id,
            title=str(title),
            episodes=str(episodes),
            year=str(year),
            season=str(season),
            subtitle_group=str(subtitle_group),
            url=f"https://{ANIME1_ME_HOST}/?cat={anime_id}",
        )

    # Anime1 marks anime1.pw entries with ID 0 and stores an anchor tag in the
    # title column instead of a plain title, so the real URL lives in the markup.
    anchor = parse_html(str(title)).a
    href = anchor.get("href") if anchor is not None else None
    if anchor is None or not isinstance(href, str):
        LOGGER.warning("Skipping anime1.pw row without a usable link: %r", row)
        return None

    return AnimeEntry(
        anime_id=anime_id,
        title=anchor.get_text(strip=True),
        episodes=str(episodes),
        year=str(year),
        season=str(season),
        subtitle_group=str(subtitle_group),
        url=href.strip(),
    )


def search_catalog(entries: Iterable[AnimeEntry], keyword: str) -> list[AnimeEntry]:
    """Return catalogue entries whose title contains keyword, ignoring case."""

    needle = keyword.casefold()
    return [entry for entry in entries if needle in entry.title.casefold()]
