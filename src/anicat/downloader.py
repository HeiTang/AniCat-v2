from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, Protocol

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
WriteMode = Literal["ab", "wb"]


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
    part_path = _partial_path(path)

    skipped = _skipped_result(episode, path, overwrite=overwrite)
    if skipped is not None:
        return skipped

    resume_from = _prepare_partial_file(path, part_path, resume=resume, overwrite=overwrite)
    response = _open_video_stream(client, episode, resume_from)

    try:
        resume_from = _normalize_resume_state(response, part_path, resume_from)
        total_bytes = _total_bytes(response.headers, resume_from, response.status_code)
        written = _write_response_body(
            response,
            part_path,
            episode,
            mode=_write_mode(resume_from),
            chunk_size=chunk_size,
            resume_from=resume_from,
            total_bytes=total_bytes,
            progress=progress,
        )
        _ensure_complete(episode, written, total_bytes)
        part_path.replace(path)
        return _downloaded_result(episode, path, written, total_bytes)
    finally:
        response.close()


def _partial_path(path: Path) -> Path:
    """Return the temporary .part path for a final video path."""

    return path.with_name(f"{path.name}.part")


def _skipped_result(
    episode: Episode,
    path: Path,
    *,
    overwrite: bool,
) -> DownloadResult | None:
    """Return a skipped result when a completed file already exists."""

    if overwrite or not path.exists():
        return None

    existing_size = path.stat().st_size
    return DownloadResult(
        episode=episode,
        path=path,
        status="skipped",
        bytes_written=existing_size,
        total_bytes=existing_size,
    )


def _prepare_partial_file(
    path: Path,
    part_path: Path,
    *,
    resume: bool,
    overwrite: bool,
) -> int:
    """Prepare existing output files and return the byte offset to resume from."""

    if overwrite:
        path.unlink(missing_ok=True)
        part_path.unlink(missing_ok=True)
        return 0

    if not resume:
        part_path.unlink(missing_ok=True)
        return 0

    return part_path.stat().st_size if part_path.exists() else 0


def _open_video_stream(
    client: VideoSource,
    episode: Episode,
    resume_from: int,
) -> VideoStreamResponse:
    """Open a video stream, adding a Range header when resuming a partial file."""

    return client.stream_video(
        episode.stream_url,
        cookies=episode.cookies,
        headers=_range_header(resume_from),
    )


def _range_header(resume_from: int) -> dict[str, str]:
    """Return HTTP Range headers for resumed downloads."""

    return {"Range": f"bytes={resume_from}-"} if resume_from else {}


def _normalize_resume_state(
    response: VideoStreamResponse,
    part_path: Path,
    resume_from: int,
) -> int:
    """Reset a partial file when the server ignores a resume Range request."""

    if resume_from and response.status_code == 200:
        part_path.unlink(missing_ok=True)
        return 0
    return resume_from


def _write_mode(resume_from: int) -> WriteMode:
    """Return append mode only when the server accepted a resumed download."""

    return "ab" if resume_from else "wb"


def _write_response_body(
    response: VideoStreamResponse,
    part_path: Path,
    episode: Episode,
    *,
    mode: WriteMode,
    chunk_size: int,
    resume_from: int,
    total_bytes: int | None,
    progress: ProgressCallback | None,
) -> int:
    """Write a streaming response body to the .part file and emit progress."""

    written = resume_from
    _emit_progress(
        progress,
        episode,
        phase="started",
        bytes_delta=0,
        bytes_completed=written,
        total_bytes=total_bytes,
    )

    with part_path.open(mode) as file:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            file.write(chunk)
            written += len(chunk)
            _emit_progress(
                progress,
                episode,
                phase="advanced",
                bytes_delta=len(chunk),
                bytes_completed=written,
                total_bytes=total_bytes,
            )

    return written


def _emit_progress(
    progress: ProgressCallback | None,
    episode: Episode,
    *,
    phase: Literal["started", "advanced"],
    bytes_delta: int,
    bytes_completed: int,
    total_bytes: int | None,
) -> None:
    """Emit one progress event when a progress callback is configured."""

    if not progress:
        return

    progress(
        DownloadProgressEvent(
            episode=episode,
            phase=phase,
            bytes_delta=bytes_delta,
            bytes_completed=bytes_completed,
            total_bytes=total_bytes,
        )
    )


def _ensure_complete(
    episode: Episode,
    written: int,
    total_bytes: int | None,
) -> None:
    """Raise when the downloaded byte count does not match response metadata."""

    if total_bytes is not None and written != total_bytes:
        raise DownloadError(
            f"incomplete download for {episode.title}: {written}/{total_bytes} bytes"
        )


def _downloaded_result(
    episode: Episode,
    path: Path,
    bytes_written: int,
    total_bytes: int | None,
) -> DownloadResult:
    """Build the successful download result after atomic promotion."""

    return DownloadResult(
        episode=episode,
        path=path,
        status="downloaded",
        bytes_written=bytes_written,
        total_bytes=total_bytes,
    )


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
