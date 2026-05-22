from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from .errors import DownloadError
from .models import DownloadProgressEvent, DownloadResult, Episode, VideoStreamResponse

DEFAULT_CHUNK_SIZE = 512 * 1024
INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_FILENAME_STEM_LENGTH = 180
WINDOWS_RESERVED_NAMES = {
    # Avoid names that are invalid on Windows even when they look like normal stems.
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

ProgressCallback = Callable[[DownloadProgressEvent], None]


class VideoSource(Protocol):
    """Minimal HTTP dependency required by the downloader."""

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> VideoStreamResponse:
        """Return a streaming video response for the given CDN URL."""

        ...


def sanitize_filename(
    value: str,
    *,
    fallback: str = "episode",
    max_length: int = MAX_FILENAME_STEM_LENGTH,
) -> str:
    """Return a cross-platform safe filename stem for an episode title."""

    cleaned = INVALID_FILENAME_PATTERN.sub("_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:max_length].strip(" .")
    if not cleaned or cleaned.upper() in WINDOWS_RESERVED_NAMES:
        return fallback
    return cleaned


def target_path(output_dir: Path, title: str) -> Path:
    """Build the final MP4 output path for an episode title."""

    return output_dir / f"{sanitize_filename(title)}.mp4"


def download_episode(
    client: VideoSource,
    episode: Episode,
    output_dir: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    resume: bool = True,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download one episode with atomic writes, resume support, and validation."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = target_path(output_dir, episode.title)
    part_path = path.with_name(f"{path.name}.part")

    if path.exists() and not overwrite:
        existing_size = path.stat().st_size
        return DownloadResult(
            episode=episode,
            path=path,
            status="skipped",
            bytes_written=existing_size,
            total_bytes=existing_size,
        )

    if overwrite:
        path.unlink(missing_ok=True)
        part_path.unlink(missing_ok=True)
    elif not resume:
        part_path.unlink(missing_ok=True)

    resume_from = part_path.stat().st_size if resume and part_path.exists() else 0
    # Resume with HTTP Range when a partial file exists; fall back below if ignored.
    request_headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    response = client.stream_video(
        episode.stream_url,
        cookies=episode.cookies,
        headers=request_headers,
    )

    try:
        if resume_from and response.status_code == 200:
            # Some servers ignore Range. Restart cleanly instead of appending duplicates.
            part_path.unlink(missing_ok=True)
            resume_from = 0

        mode = "ab" if resume_from else "wb"
        total_bytes = _total_bytes(response.headers, resume_from, response.status_code)
        written = resume_from
        if progress:
            # Emit the initial state so UIs can create a per-file task before bytes move.
            progress(
                DownloadProgressEvent(
                    episode=episode,
                    phase="started",
                    bytes_delta=0,
                    bytes_completed=written,
                    total_bytes=total_bytes,
                )
            )

        with part_path.open(mode) as file:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                file.write(chunk)
                written += len(chunk)
                if progress:
                    progress(
                        DownloadProgressEvent(
                            episode=episode,
                            phase="advanced",
                            bytes_delta=len(chunk),
                            bytes_completed=written,
                            total_bytes=total_bytes,
                        )
                    )

        if total_bytes is not None and written != total_bytes:
            raise DownloadError(
                f"incomplete download for {episode.title}: {written}/{total_bytes} bytes"
            )

        # Atomic promotion prevents interrupted downloads from masquerading as complete files.
        part_path.replace(path)
        return DownloadResult(
            episode=episode,
            path=path,
            status="downloaded",
            bytes_written=written,
            total_bytes=total_bytes,
        )
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def _total_bytes(
    headers: Mapping[str, str],
    resume_from: int,
    status_code: int,
) -> int | None:
    """Return expected final file size from response headers when available."""

    if status_code == 206:
        # For resumed responses, Content-Length is only the remaining byte count.
        content_range = headers.get("content-range") or headers.get("Content-Range")
        if content_range:
            _, _, total = content_range.partition("/")
            if total.isdigit():
                return int(total)

    content_length = headers.get("content-length") or headers.get("Content-Length")
    if content_length and content_length.isdigit():
        return resume_from + int(content_length)
    return None
