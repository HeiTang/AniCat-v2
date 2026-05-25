from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, FeatureNotFound

from .errors import ParseError
from .models import Episode
from .urls import ANIME1_ME_SOURCE, ANIME1_PW_SOURCE, SourceKind, source_kind

ACCESS_COOKIE_NAMES = ("e", "p", "h")
SET_COOKIE_SEPARATOR_PATTERN = re.compile(r",\s*(?=[^=;,\s]+=)")
LOGGER = logging.getLogger(__name__)
PageFetcher = Callable[[str], str]


@dataclass(frozen=True)
class SeasonPage:
    """Parsed season/category page with episode links and pagination."""

    episode_urls: list[str]
    next_url: str | None


class EpisodeSource(Protocol):
    """Minimal HTTP dependency required by Anime1Extractor."""

    def get_page(self, url: str) -> str:
        """Return raw HTML for an Anime1 page fetched with GET."""

        ...

    def post_page(self, url: str) -> str:
        """Return raw HTML for an Anime1 page fetched with POST."""

        ...

    def post_api(self, data_apireq: str) -> requests.Response:
        """Return raw response for an Anime1 episode API payload."""

        ...


class EpisodeExtractor(Protocol):
    """Provider-specific extraction behavior selected by Anime1Extractor."""

    def season_episode_urls(self, url: str) -> list[str]:
        """Collect all episode URLs from a supported season/category URL."""

        ...

    def episode(self, url: str) -> Episode:
        """Resolve one episode page URL into download-ready metadata."""

        ...


def parse_season_page(html: str) -> SeasonPage:
    """Parse episode URLs and next-page URL from a season/category page."""

    soup = parse_html(html)
    episode_urls: list[str] = []
    for anchor in soup.select("h2.entry-title a[rel='bookmark']"):
        href = anchor.get("href")
        if isinstance(href, str):
            episode_urls.append(href)

    next_anchor = soup.select_one("div.nav-previous a[href]")
    next_href = next_anchor.get("href") if next_anchor else None
    return SeasonPage(
        episode_urls=episode_urls,
        next_url=next_href if isinstance(next_href, str) else None,
    )


def parse_episode_page(html: str) -> tuple[str, str]:
    """Parse data-apireq and display title from an episode page."""

    soup = parse_html(html)
    title = soup.select_one("h2.entry-title")

    data_apireq = select_episode_api_request(soup)
    if data_apireq is None:
        raise ParseError("episode page is missing video data-apireq")
    if not title:
        raise ParseError("episode page is missing title")

    return data_apireq, title.get_text(" ", strip=True)


def parse_direct_episode_page(html: str, base_url: str) -> tuple[str, str]:
    """Parse direct-video episode pages that expose a source URL in HTML."""

    soup = parse_html(html)
    title = soup.select_one("h1.entry-title") or soup.select_one("h2.entry-title")
    source_url = select_direct_video_source(soup)

    if not title:
        raise ParseError("episode page is missing title")
    if source_url is None:
        raise ParseError("episode page is missing MP4 video source")

    return urljoin(base_url, source_url), title.get_text(" ", strip=True)


def select_direct_video_source(soup: BeautifulSoup) -> str | None:
    """Return the first MP4-compatible direct video source URL."""

    candidates: list[tuple[str, str | None]] = []
    for source in soup.select("video.video-js source[src]"):
        source_url = source.get("src")
        if not isinstance(source_url, str) or not source_url:
            continue
        candidates.append((source_url, source_media_type(source_url, source.get("type"))))

    mp4_candidates = [
        source_url for source_url, source_type in candidates if source_type == "video/mp4"
    ]

    if len(candidates) > 1:
        LOGGER.warning(
            "episode page contains %d source candidates; %d MP4-compatible",
            len(candidates),
            len(mp4_candidates),
        )

    return mp4_candidates[0] if mp4_candidates else None


def source_media_type(source_url: str, value: object) -> str | None:
    """Return the declared media type or infer MP4 from the source URL path."""

    source_type = media_type(value)
    if source_type is not None:
        return source_type
    if urlparse(source_url).path.lower().endswith(".mp4"):
        return "video/mp4"
    return None


def media_type(value: object) -> str | None:
    """Normalize a source type attribute into a comparable media type."""

    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(";", 1)[0].strip().lower()


def select_episode_api_request(soup: BeautifulSoup) -> str | None:
    """Return the first valid video data-apireq from an episode page."""

    candidates = [
        data_apireq
        for video in soup.select("video.video-js")
        if isinstance(data_apireq := video.get("data-apireq"), str) and data_apireq
    ]
    if len(candidates) > 1:
        LOGGER.warning(
            "episode page contains %d video candidates; using the first", len(candidates)
        )
    return candidates[0] if candidates else None


def parse_html(html: str) -> BeautifulSoup:
    """Create a BeautifulSoup document with lxml fallback handling."""

    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        # Keep the CLI usable even when the optional native lxml parser is missing.
        return BeautifulSoup(html, "html.parser")


def parse_stream_url(payload: Any) -> str:
    """Extract the video stream URL from Anime1 API response JSON."""

    stream = payload.get("s") if isinstance(payload, dict) else None
    if isinstance(stream, list):
        stream = stream[0] if stream else None
    if not isinstance(stream, dict) or not stream.get("src"):
        raise ParseError(f"API response is missing stream src: {payload}")
    return stream["src"]


def extract_access_cookies(response: requests.Response) -> dict[str, str]:
    """Extract video access cookies required by the CDN request."""

    # requests can parse some Set-Cookie layouts directly; Anime1 also returns
    # multiple comma-separated HttpOnly cookies that need a header fallback.
    cookies = {name: value for name in ACCESS_COOKIE_NAMES if (value := response.cookies.get(name))}

    if len(cookies) == len(ACCESS_COOKIE_NAMES):
        return cookies

    parsed = parse_set_cookie_header(response.headers.get("set-cookie", ""))

    for name in ACCESS_COOKIE_NAMES:
        if name not in cookies and name in parsed:
            cookies[name] = parsed[name].value

    missing = [name for name in ACCESS_COOKIE_NAMES if name not in cookies]
    if missing:
        raise ParseError(f"API response is missing access cookies: {', '.join(missing)}")

    return cookies


def parse_set_cookie_header(header: str) -> SimpleCookie:
    """Parse a possibly comma-joined Set-Cookie header into a SimpleCookie."""

    parsed = SimpleCookie()
    if not header:
        return parsed

    try:
        parsed.load(header)
    except CookieError:
        parsed = SimpleCookie()

    if parsed:
        return parsed

    for item in split_combined_set_cookie_header(header):
        try:
            parsed.load(item)
        except CookieError:
            continue
    return parsed


def split_combined_set_cookie_header(header: str) -> list[str]:
    """Split requests-style comma-joined Set-Cookie headers into cookie fields."""

    return [item.strip() for item in SET_COOKIE_SEPARATOR_PATTERN.split(header) if item.strip()]


def collect_paginated_episode_urls(fetch_page: PageFetcher, url: str) -> list[str]:
    """Collect episode links from a paginated WordPress-style listing."""

    collected: list[str] = []
    current_url: str | None = url
    visited: set[str] = set()

    while current_url:
        if current_url in visited:
            raise ParseError(f"season pagination loop detected: {current_url}")
        visited.add(current_url)

        html = fetch_page(current_url)
        page = parse_season_page(html)
        if html.strip() and not page.episode_urls:
            raise ParseError(f"season page returned no episode links: {current_url}")
        collected.extend(urljoin(current_url, item) for item in page.episode_urls)
        # Resolve relative pagination URLs against the page that produced them.
        current_url = urljoin(current_url, page.next_url) if page.next_url else None

    return collected


class Anime1MeExtractor:
    """Extractor for anime1.me pages that require the Anime1 API."""

    def __init__(self, client: EpisodeSource) -> None:
        self.client = client

    def season_episode_urls(self, url: str) -> list[str]:
        """Collect all anime1.me episode URLs from a category URL."""

        return collect_paginated_episode_urls(self.client.post_page, url)

    def episode(self, url: str) -> Episode:
        """Resolve one anime1.me episode via page data-apireq and API response."""

        data_apireq, title = parse_episode_page(self.client.post_page(url))
        response = self.client.post_api(data_apireq)

        try:
            payload = response.json()
        except ValueError as error:
            raise ParseError(f"API response is not JSON: {response.text}") from error

        stream_url = urljoin("https://v.anime1.me", parse_stream_url(payload))
        return Episode(
            page_url=url,
            title=title,
            stream_url=stream_url,
            cookies=extract_access_cookies(response),
        )


class Anime1PwExtractor:
    """Extractor for anime1.pw pages that expose direct MP4 sources."""

    def __init__(self, client: EpisodeSource) -> None:
        self.client = client

    def season_episode_urls(self, url: str) -> list[str]:
        """Collect all anime1.pw episode URLs from a category URL."""

        return collect_paginated_episode_urls(self.client.get_page, url)

    def episode(self, url: str) -> Episode:
        """Resolve one anime1.pw episode directly from the page source tag."""

        stream_url, title = parse_direct_episode_page(self.client.get_page(url), url)
        return Episode(
            page_url=url,
            title=title,
            stream_url=stream_url,
            cookies={},
        )


class Anime1Extractor:
    """Route supported Anime1 URLs to provider-specific extractors."""

    def __init__(self, client: EpisodeSource) -> None:
        self.extractors: dict[SourceKind, EpisodeExtractor] = {
            ANIME1_ME_SOURCE: Anime1MeExtractor(client),
            ANIME1_PW_SOURCE: Anime1PwExtractor(client),
        }

    def season_episode_urls(self, url: str) -> list[str]:
        """Collect all episode URLs from a supported category URL."""

        return self._extractor_for_url(url).season_episode_urls(url)

    def episode(self, url: str) -> Episode:
        """Resolve one supported episode URL into stream URL, title, and cookies."""

        return self._extractor_for_url(url).episode(url)

    def _extractor_for_url(self, url: str) -> EpisodeExtractor:
        """Return the extractor that owns the URL's supported source kind."""

        kind = source_kind(url)
        if kind is None:
            raise ParseError(f"unsupported Anime1 URL: {url}")
        return self.extractors[kind]
