"""Download HLS with FFmpeg while keeping completed files intact on failure."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import requests

from .downloader import ProgressCallback, target_path
from .errors import DownloadError
from .hls import validate_playlist_url
from .models import DownloadProgressEvent, DownloadResult, Episode

_HLS_PROBE_BYTES = 376
_HLS_MIN_SEGMENT_BYTES = 188
_MAX_MASTER_VARIANT_DEPTH = 3


def _fetch_playlist(url: str, episode: Episode, timeout: float) -> str:
    """Fetch one HLS playlist while preserving the episode request context."""

    response: requests.Response | None = None
    try:
        response = requests.get(
            url,
            headers={"Referer": episode.page_url},
            cookies=dict(episode.cookies),
            timeout=(timeout, timeout),
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as error:
        raise DownloadError(f"could not fetch HLS playlist {url}: {error}") from error
    finally:
        if response is not None:
            response.close()


def _playlist_variants(playlist: str) -> list[tuple[int, str]]:
    """Return bandwidth and URI pairs from a master playlist."""

    variants: list[tuple[int, str]] = []
    pending_bandwidth: int | None = None
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_bandwidth = 0
            for attribute in line.partition(":")[2].split(","):
                key, separator, value = attribute.partition("=")
                if separator and key.strip().upper() == "BANDWIDTH":
                    try:
                        pending_bandwidth = int(value.strip())
                    except ValueError:
                        pending_bandwidth = 0
                    break
        elif pending_bandwidth is not None and line and not line.startswith("#"):
            variants.append((pending_bandwidth, line))
            pending_bandwidth = None
    return variants


def _playlist_segments(playlist: str) -> list[str]:
    """Return media segment and initialization URIs from a media playlist."""

    segments: list[str] = []
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MAP:"):
            uri = _playlist_attribute(line.partition(":")[2], "URI")
            if uri:
                segments.append(uri)
        elif line and not line.startswith("#"):
            segments.append(line)
    return segments


def _playlist_attribute(attributes: str, wanted: str) -> str | None:
    """Read one quoted or unquoted attribute from an HLS tag."""

    for attribute in attributes.split(","):
        key, separator, value = attribute.partition("=")
        if separator and key.strip().upper() == wanted:
            return value.strip().strip('"')
    return None


def _probe_segment(url: str, episode: Episode, timeout: float) -> None:
    """Verify that a segment exists and is not an obviously truncated response."""

    response: requests.Response | None = None
    try:
        response = requests.get(
            url,
            headers={"Referer": episode.page_url, "Range": f"bytes=0-{_HLS_PROBE_BYTES - 1}"},
            cookies=dict(episode.cookies),
            stream=True,
            timeout=(timeout, timeout),
        )
        response.raise_for_status()
        content_range = response.headers.get("Content-Range", "")
        total = None
        if "/" in content_range:
            try:
                total_text = content_range.rsplit("/", 1)[1]
                total = None if total_text == "*" else int(total_text)
            except ValueError:
                total = None
        if total is None:
            content_length = response.headers.get("Content-Length")
            try:
                total = int(content_length) if content_length is not None else None
            except ValueError:
                total = None

        sample = next(response.iter_content(chunk_size=_HLS_PROBE_BYTES), b"")
        sample = sample[:_HLS_PROBE_BYTES]
        if len(sample) < _HLS_MIN_SEGMENT_BYTES or (
            total is not None and total < _HLS_MIN_SEGMENT_BYTES
        ):
            raise DownloadError(f"HLS segment is truncated or empty: {url}")
        if sample[:1] == b"G" and len(sample) >= 2 * 188 and sample[188:189] != b"G":
            raise DownloadError(f"HLS MPEG-TS segment is malformed: {url}")
    except requests.RequestException as error:
        raise DownloadError(f"could not fetch HLS segment {url}: {error}") from error
    finally:
        if response is not None:
            response.close()


def _validate_hls_sources(playlist_url: str, episode: Episode, timeout: float) -> None:
    """Reject playlists with missing or truncated media before making an MP4."""

    current_url = playlist_url
    playlist = _fetch_playlist(current_url, episode, timeout)
    for _ in range(_MAX_MASTER_VARIANT_DEPTH):
        variants = _playlist_variants(playlist)
        if not variants:
            break
        _, variant = max(variants, key=lambda item: item[0])
        current_url = urljoin(current_url, variant)
        playlist = _fetch_playlist(current_url, episode, timeout)

    segments = _playlist_segments(playlist)
    if not segments:
        raise DownloadError(f"HLS playlist contains no media segments: {current_url}")
    for segment in segments:
        _probe_segment(urljoin(current_url, segment), episode, timeout)


def download_hls_episode(
    episode: Episode,
    output_dir: Path,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
    timeout: float = 30,
    retries: int = 3,
) -> DownloadResult:
    """Remux a playlist to MP4 with bounded full-session retries."""

    path = target_path(output_dir, episode.title)
    if path.exists() and not overwrite:
        size = path.stat().st_size
        return DownloadResult(episode, path, "skipped", size, size)
    playlist = validate_playlist_url(episode.stream_url, episode.page_url)
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise DownloadError("HLS downloads require FFmpeg. Install ffmpeg and add it to PATH.")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    if retries < 0:
        raise ValueError("retries must be greater than or equal to 0")
    if any(character in episode.page_url for character in "\r\n"):
        raise DownloadError("invalid episode Referer")
    _validate_hls_sources(playlist, episode, timeout)
    output_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(DownloadProgressEvent(episode, "started", 0, 0))
    # HLS cannot reuse the direct downloader's byte-range .part files.
    with tempfile.TemporaryDirectory(prefix=".anicat-hls-", dir=output_dir) as work:
        temporary = Path(work) / "video.mp4"
        command = [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-rw_timeout",
            str(int(timeout * 1_000_000)),
            "-referer",
            episode.page_url,
            "-i",
            playlist,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "copy",
            # Some providers repeat audio timestamps at HLS discontinuities.
            "-c:a",
            "aac",
            "-af",
            "aresample=async=1:first_pts=0",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(temporary),
        ]
        last_error = "FFmpeg produced no video"
        for attempt in range(retries + 1):
            temporary.unlink(missing_ok=True)
            # Keep FFmpeg diagnostics on disk so long failures cannot exhaust memory.
            with tempfile.TemporaryFile() as diagnostics:
                try:
                    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=diagnostics)
                except subprocess.TimeoutExpired:
                    result = subprocess.CompletedProcess(command, -1)
                    detail = f"timed out after {timeout:g} seconds"
                except OSError as error:
                    raise DownloadError(f"could not start FFmpeg: {error}") from error
                else:
                    diagnostics.seek(0, 2)
                    diagnostics.seek(max(0, diagnostics.tell() - 2000))
                    detail = diagnostics.read().decode("utf-8", errors="replace").strip()

            if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size > 0:
                break

            last_error = (
                f"FFmpeg failed ({result.returncode}): {detail}"
                if result.returncode
                else "FFmpeg produced no video"
            )
            if attempt == retries:
                raise DownloadError(f"{last_error} after {attempt + 1} attempt(s)")
        else:
            raise DownloadError(f"{last_error} after {retries + 1} attempt(s)")
        size = temporary.stat().st_size
        temporary.replace(path)
    # shortcut: report final bytes only; use FFmpeg -progress for live HLS byte progress.
    if progress:
        progress(DownloadProgressEvent(episode, "advanced", size, size, size))
    return DownloadResult(episode, path, "downloaded", size, size)
