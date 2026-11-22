"""Parse the HLS player pages used by anime1.cc and anime1.in."""

from urllib.parse import parse_qs, urljoin, urlparse

from .errors import ParseError
from .extractor import PageFetcher, parse_html
from .urls import dedupe, is_episode_url, source_kind


def validate_playlist_url(value: str, base_url: str) -> str:
    """Accept only HTTP playlists, never local files or executable protocols."""

    url = urljoin(base_url, value)
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.lower().endswith(".m3u8")
        or any(character.isspace() for character in value)
    ):
        raise ParseError("player contains an invalid HLS playlist URL")
    return url


def parse_hls_episode(html: str, page_url: str, fetch_page: PageFetcher) -> tuple[str, str]:
    """Resolve a supported player iframe into a playlist and episode title."""

    soup = parse_html(html)
    title = soup.select_one("h1.entry-title") or soup.select_one("h2.entry-title")
    if title is None or not title.get_text(strip=True):
        raise ParseError("episode page is missing title")

    players = [iframe.get("src") for iframe in soup.select("iframe[src]")]
    if source_kind(page_url) == "anime1_in" and urlparse(page_url).fragment == "other":
        alternatives = soup.select("button.play-select[url]")
        if len(alternatives) < 2:
            raise ParseError("requested alternate player is missing from the episode page")
        players = [alternatives[1].get("url")]

    for src in players:
        if not isinstance(src, str):
            continue
        player_url = urljoin(page_url, src)
        player = urlparse(player_url)
        if (
            player.scheme not in {"http", "https"}
            or player.netloc != urlparse(page_url).netloc
            or player.username is not None
            or player.password is not None
        ):
            continue
        if player.path == "/_player":
            values = parse_qs(player.query).get("url", [])
            if values:
                return validate_playlist_url(values[0], player_url), title.get_text(" ", strip=True)
        elif player.path.startswith(("/_player_x_/", "/_player_y_/")):
            player_soup = parse_html(fetch_page(player_url))
            for source in player_soup.select("video source[src]"):
                value = source.get("src")
                if isinstance(value, str) and urlparse(value).path.lower().endswith(".m3u8"):
                    return validate_playlist_url(value, player_url), title.get_text(" ", strip=True)
    raise ParseError("episode page is missing a supported HLS player")


def collect_hls_episode_urls(fetch_page: PageFetcher, url: str) -> list[str]:
    """Expand the provider's episode list, following only same-series pagination."""

    parsed = urlparse(url)
    series = parsed.path.strip("/").split("/")[0]
    root = f"{parsed.scheme}://{parsed.netloc}/{series}/"
    current: str | None = url
    visited: set[str] = set()
    episodes: list[str] = []
    is_cc = source_kind(url) == "anime1_cc"
    while current:
        if current in visited:
            raise ParseError("season pagination loop detected")
        visited.add(current)
        soup = parse_html(fetch_page(current))
        selector = (
            ".entry-content ul.eps a.e-aa[href]" if is_cc else "h2.entry-title a[rel=bookmark]"
        )
        found: list[str] = []
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            candidate = urljoin(current, href).split("#", 1)[0]
            target = urlparse(candidate)
            if (
                target.netloc == parsed.netloc
                and (is_cc or target.path.startswith(f"/{series}-"))
                and is_episode_url(candidate)
            ):
                found.append(candidate)
        if not found:
            raise ParseError(f"season page returned no episode links: {current}")
        episodes.extend(found)
        if is_cc:
            next_anchor = next(
                (
                    a
                    for a in soup.select(".entry-content a[href]")
                    if a.get_text(strip=True) == "下一頁"
                ),
                None,
            )
        else:
            next_anchor = soup.select_one("div.nav-previous a[href]")
        href = next_anchor.get("href") if next_anchor else None
        current = urljoin(root if is_cc else current, href) if isinstance(href, str) else None
        if current:
            target = urlparse(current)
            suffix = target.path.removeprefix(f"/{series}/").strip("/")
            page_number = suffix if is_cc else suffix.removeprefix("page/")
            if (
                target.netloc != parsed.netloc
                or target.scheme != parsed.scheme
                or not target.path.startswith(f"/{series}/")
                or not page_number.isdigit()
            ):
                raise ParseError("season pagination points outside the requested series")
    return dedupe(episodes)
