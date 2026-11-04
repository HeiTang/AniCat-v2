"""Download HLS with FFmpeg while keeping completed files intact on failure."""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests

from .downloader import ProgressCallback, target_path
from .errors import DownloadError
from .hls import validate_playlist_url
from .models import DownloadProgressEvent, DownloadResult, Episode

_MAX_MASTER_VARIANT_DEPTH = 3
_HLS_DURATION_TOLERANCE = 0.25


@dataclass(frozen=True)
class HlsManifest:
    """Media playlist facts used to reject incomplete remuxes."""

    url: str
    segment_count: int
    expected_duration: float | None


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


def _playlist_duration(playlist: str) -> float | None:
    """Return the sum of media durations, when the playlist declares them."""

    durations = [
        float(match.group(1))
        for match in re.finditer(r"#EXTINF:([0-9]+(?:\.[0-9]+)?)[,\r\n]", playlist)
    ]
    return sum(durations) if durations else None


def _validate_hls_sources(playlist_url: str, episode: Episode, timeout: float) -> HlsManifest:
    """Inspect the final media playlist before making an MP4."""

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
    if "#EXT-X-ENDLIST" not in playlist:
        raise DownloadError(f"HLS playlist is not complete: {current_url}")
    return HlsManifest(current_url, len(segments), _playlist_duration(playlist))


def _progress_duration(output: bytes | str | None) -> float | None:
    """Read the final FFmpeg progress timestamp from stdout."""

    if output is None:
        return None
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    matches = re.findall(r"^out_time_us=(\d+)$", text, flags=re.MULTILINE)
    return int(matches[-1]) / 1_000_000 if matches else None


def _duration_matches(expected: float, actual: float) -> bool:
    """Allow only small timestamp rounding differences between HLS and MP4."""

    tolerance = max(_HLS_DURATION_TOLERANCE, expected * 0.001)
    return abs(expected - actual) < tolerance


def _safe_audio_timestamp_correction(line: str) -> bool:
    """Allow only sub-100ms AAC mux rounding; reject skipped segments and corruption."""

    match = re.search(
        r"Non-monotonous DTS in output stream 0:1; "
        r"previous: (\d+), current: (\d+); changing to (\d+)",
        line,
    )
    if match is None:
        return False
    previous, current, corrected = map(int, match.groups())
    return 0 <= previous - current <= 4800 and corrected == previous + 1


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
    manifest: HlsManifest | None = None
    for preflight_attempt in range(retries + 1):
        try:
            manifest = _validate_hls_sources(playlist, episode, timeout)
            break
        except DownloadError as error:
            if preflight_attempt == retries:
                raise DownloadError(
                    f"{error} after {preflight_attempt + 1} preflight attempt(s)"
                ) from error
    expected_duration = manifest.expected_duration if manifest else None
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
            "warning",
            "-err_detect",
            "explode",
            "-nostats",
            "-progress",
            "pipe:1",
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-rw_timeout",
            str(int(timeout * 1_000_000)),
            # Segment redirects may switch CDN hosts; do not reuse their TLS connection.
            "-http_persistent",
            "0",
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
            "-ar",
            "48000",
            "-ac",
            "2",
            "-af",
            "aresample=48000:async=1",
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
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=diagnostics)
                except OSError as error:
                    raise DownloadError(f"could not start FFmpeg: {error}") from error
                else:
                    diagnostics.seek(0)
                    detail = ""
                    for raw_line in diagnostics:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if line and not _safe_audio_timestamp_correction(line):
                            detail = line[:2000]
                            break

            duration = _progress_duration(result.stdout)
            complete = (
                result.returncode == 0
                and not detail
                and temporary.is_file()
                and temporary.stat().st_size > 0
            )
            if complete and expected_duration is not None:
                complete = duration is not None and _duration_matches(expected_duration, duration)
                if not complete:
                    last_error = (
                        "FFmpeg output duration mismatch: "
                        f"expected {expected_duration:.3f}s, "
                        f"got {duration if duration is not None else 'unknown'}s"
                    )
            if complete:
                break

            if result.returncode or detail:
                last_error = f"FFmpeg failed ({result.returncode}): {detail}"
            elif duration is None or expected_duration is None:
                last_error = "FFmpeg produced no video"
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
