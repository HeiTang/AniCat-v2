from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


class VideoStreamResponse(Protocol):
    """Minimal streaming response surface used by the downloader."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status code."""

        ...

    @property
    def headers(self) -> Mapping[str, str]:
        """Return response headers."""

        ...

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        """Yield response body chunks."""

        ...

    def close(self) -> None:
        """Release the underlying response resources."""

        ...


@dataclass(frozen=True)
class AnimeEntry:
    """One anime series listed in the Anime1 catalogue index."""

    anime_id: int
    title: str
    episodes: str
    year: str
    season: str
    subtitle_group: str
    url: str


@dataclass(frozen=True)
class Episode:
    """Resolved metadata required to download a single Anime1 episode."""

    page_url: str
    title: str
    stream_url: str
    cookies: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a completed or skipped episode download."""

    episode: Episode
    path: Path
    status: Literal["downloaded", "skipped"]
    bytes_written: int
    total_bytes: int | None = None


@dataclass(frozen=True)
class JobReport:
    """Per-URL job summary returned by the orchestration layer."""

    url: str
    result: DownloadResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class DownloadProgressEvent:
    """Byte-level progress event emitted by the downloader.

    The reset phase means previously resumed bytes were discarded because the
    upstream server ignored a Range request and forced a full restart.
    """

    episode: Episode
    phase: Literal["started", "advanced", "reset"]
    bytes_delta: int
    bytes_completed: int
    total_bytes: int | None = None
