from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Protocol
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, FeatureNotFound

from .errors import ParseError
from .models import Episode

ACCESS_COOKIE_NAMES = ("e", "p", "h")


@dataclass(frozen=True)
class SeasonPage:
    """Parsed season/category page with episode links and pagination."""

    episode_urls: list[str]
    next_url: str | None


class EpisodeSource(Protocol):
    """Minimal HTTP dependency required by Anime1Extractor."""

    def post_page(self, url: str) -> str:
        """Return raw HTML for an Anime1 page."""

        ...

    def post_api(self, data_apireq: str) -> requests.Response:
        """Return raw response for an Anime1 episode API payload."""

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
    video = soup.select_one("video.video-js")
    title = soup.select_one("h2.entry-title")

    data_apireq = video.get("data-apireq") if video else None
    if not isinstance(data_apireq, str):
        raise ParseError("episode page is missing video data-apireq")
    if not title:
        raise ParseError("episode page is missing title")

    return data_apireq, title.get_text(" ", strip=True)


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

    parsed = SimpleCookie()
    try:
        parsed.load(response.headers.get("set-cookie", ""))
    except Exception:
        parsed = SimpleCookie()

    for name in ACCESS_COOKIE_NAMES:
        if name not in cookies and name in parsed:
            cookies[name] = parsed[name].value

    missing = [name for name in ACCESS_COOKIE_NAMES if name not in cookies]
    if missing:
        raise ParseError(f"API response is missing access cookies: {', '.join(missing)}")

    return cookies


class Anime1Extractor:
    """Convert Anime1 HTML/API responses into download-ready domain objects."""

    def __init__(self, client: EpisodeSource) -> None:
        self.client = client

    def season_episode_urls(self, url: str) -> list[str]:
        """Collect all episode URLs from a paginated season/category URL."""

        collected: list[str] = []
        current_url: str | None = url
        visited: set[str] = set()

        while current_url:
            if current_url in visited:
                raise ParseError(f"season pagination loop detected: {current_url}")
            visited.add(current_url)

            page = parse_season_page(self.client.post_page(current_url))
            collected.extend(urljoin(current_url, item) for item in page.episode_urls)
            # Resolve relative pagination URLs against the page that produced them.
            current_url = urljoin(current_url, page.next_url) if page.next_url else None

        return collected

    def episode(self, url: str) -> Episode:
        """Resolve an episode page URL into stream URL, title, and cookies."""

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
