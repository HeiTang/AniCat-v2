"""Download HLS with FFmpeg while keeping completed files intact on failure."""

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin

import requests

from .downloader import ProgressCallback, target_path
from .errors import DownloadError
from .ffmpeg import find_ffmpeg
from .hls import validate_playlist_url
from .hls_segments import (
    SegmentSpec,
    download_segments,
    parse_media_segments,
    write_group_playlists,
    write_local_playlist,
)
from .models import DownloadProgressEvent, DownloadResult, Episode

_MAX_MASTER_VARIANT_DEPTH = 3
_HLS_DURATION_TOLERANCE = 0.25
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HlsManifest:
    """Media playlist facts used to reject incomplete remuxes."""

    url: str
    segment_count: int
    expected_duration: float | None
    segments: tuple[SegmentSpec, ...] | None = None


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

    if "#EXT-X-ENDLIST" not in playlist:
        raise DownloadError(f"HLS playlist is not complete: {current_url}")
    segments = parse_media_segments(playlist, current_url)
    return HlsManifest(
        current_url,
        len(segments),
        _playlist_duration(playlist),
        segments,
    )


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

    legacy = re.search(
        r"Non-monotonous DTS in output stream 0:1; "
        r"previous: (\d+), current: (\d+); changing to (\d+)",
        line,
    )
    modern = re.search(
        r"\[aost#0:1/copy(?:\s+@[^\]]+)?\]\s+Non-monotonic DTS; "
        r"previous: (\d+), current: (\d+); changing to (\d+)",
        line,
    )
    match = legacy or modern
    if match is None:
        return False
    previous, current, corrected = map(int, match.groups())
    return 0 <= previous - current <= 4800 and corrected == previous + 1


def _classify_ffmpeg_diagnostic(line: str) -> str:
    """Classify every FFmpeg warning; only the measured AAC mux correction is recoverable."""

    if _safe_audio_timestamp_correction(line):
        return "recoverable_audio_timestamp_rounding"
    if "Packet corrupt" in line:
        return "fatal_packet_corruption"
    if "Non-monotonous DTS" in line:
        return "fatal_timestamp_discontinuity"
    if "error" in line.lower() or "failed" in line.lower():
        return "fatal_ffmpeg_error"
    return "fatal_ffmpeg_warning"


def _read_ffmpeg_diagnostics(
    diagnostics: BinaryIO,
) -> tuple[str, tuple[str, ...]]:
    """Classify all FFmpeg diagnostics and retain recoverable lines for evidence."""

    diagnostics.seek(0)
    fatal: list[str] = []
    recoverable: list[str] = []
    for raw_line in diagnostics:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        category = _classify_ffmpeg_diagnostic(line)
        if category == "recoverable_audio_timestamp_rounding":
            recoverable.append(f"[{category}] {line}")
        else:
            fatal.append(f"[{category}] {line}")
    return "\n".join(fatal)[:2000], tuple(recoverable)


def _run_ffmpeg(
    command: list[str],
) -> tuple[subprocess.CompletedProcess[bytes], str, tuple[str, ...]]:
    """Run one FFmpeg job and return fatal/recoverable diagnostic evidence."""

    with tempfile.TemporaryFile() as diagnostics:
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=diagnostics)
        except OSError as error:
            raise DownloadError(f"could not start FFmpeg: {error}") from error
        detail, recoverable = _read_ffmpeg_diagnostics(diagnostics)
    return result, detail, recoverable


def _remux_command(
    executable: str,
    input_path: str,
    output_path: Path,
    page_url: str,
    timeout: float,
) -> list[str]:
    """Build one local or remote HLS-to-MP4 remux command."""

    input_headers = ["-referer", page_url] if input_path.startswith(("http://", "https://")) else []
    return [
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
        "file,pipe,http,https,tcp,tls,crypto",
        "-rw_timeout",
        str(int(timeout * 1_000_000)),
        # Segment redirects may switch CDN hosts; do not reuse their TLS connection.
        "-http_persistent",
        "0",
        *input_headers,
        "-i",
        input_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
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
        str(output_path),
    ]


def _concat_command(executable: str, manifest: Path, output_path: Path) -> list[str]:
    """Build the stream-copy concat command for independently remuxed groups."""

    return [
        executable,
        "-nostdin",
        "-hide_banner",
        "-y",
        "-loglevel",
        "warning",
        "-err_detect",
        "explode",
        "-nostats",
        "-progress",
        "pipe:1",
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _remux_error(
    result: subprocess.CompletedProcess[bytes],
    detail: str,
    output: Path,
    expected_duration: float | None,
) -> tuple[str | None, float | None]:
    """Validate one remux result before accepting it as a group or final file."""

    duration = _progress_duration(result.stdout)
    if result.returncode or detail:
        return f"FFmpeg failed ({result.returncode}): {detail}", duration
    if not output.is_file() or output.stat().st_size == 0:
        return "FFmpeg produced no video", duration
    if expected_duration is not None and (
        duration is None or not _duration_matches(expected_duration, duration)
    ):
        return (
            "FFmpeg output duration mismatch: "
            f"expected {expected_duration:.3f}s, "
            f"got {duration if duration is not None else 'unknown'}s"
        ), duration
    return None, duration


def _write_concat_manifest(path: Path, group_outputs: tuple[Path, ...]) -> None:
    """Write a concat manifest with paths relative to its own directory."""

    path.write_text(
        "".join(
            f"file '{output.relative_to(path.parent).as_posix()}'\n" for output in group_outputs
        ),
        encoding="utf-8",
    )


def _remux_groups(
    executable: str,
    group_playlists: tuple[tuple[Path, float], ...],
    work: Path,
    output: Path,
    timeout: float,
    expected_duration: float | None,
) -> str | None:
    """Remux each discontinuity group, then join compatible MP4 streams."""

    group_outputs = tuple(work / f"group-{index:03d}.mp4" for index in range(len(group_playlists)))
    for (playlist, expected), group_output in zip(group_playlists, group_outputs, strict=True):
        group_output.unlink(missing_ok=True)
        result, detail, recoverable = _run_ffmpeg(
            _remux_command(executable, str(playlist), group_output, "", timeout)
        )
        error, duration = _remux_error(result, detail, group_output, expected)
        LOGGER.debug(
            "HLS group %s duration expected=%.3f actual=%s fatal=%s recoverable=%s",
            playlist.stem,
            expected,
            f"{duration:.3f}" if duration is not None else "unknown",
            detail or "",
            recoverable,
        )
        if error is not None:
            return error
    concat_manifest = work / "concat.txt"
    _write_concat_manifest(concat_manifest, group_outputs)
    result, detail, recoverable = _run_ffmpeg(_concat_command(executable, concat_manifest, output))
    error, duration = _remux_error(result, detail, output, expected_duration)
    LOGGER.debug(
        "HLS group concat duration expected=%s actual=%s fatal=%s recoverable=%s",
        f"{expected_duration:.3f}" if expected_duration is not None else "unknown",
        f"{duration:.3f}" if duration is not None else "unknown",
        detail or "",
        recoverable,
    )
    return error


def _decode_timestamp_options(executable: str) -> tuple[str, ...]:
    """Use FFmpeg 9's replacement for flags removed from older releases."""

    try:
        version = subprocess.run(
            [executable, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    version_output = version.stdout if version is not None else b""
    major_match = re.search(rb"ffmpeg version [nN]?(\d+)", version_output)
    abi_match = re.search(rb"libavcodec\s+(\d+)", version_output)
    modern = (major_match is not None and int(major_match.group(1)) >= 9) or (
        major_match is None and abi_match is not None and int(abi_match.group(1)) >= 63
    )
    if modern:
        return ("-fps_mode", "passthrough", "-enc_time_base", "demux")
    return ("-vsync", "0", "-enc_time_base", "-1")


def _validate_output(executable: str, path: Path, duration: float | None) -> None:
    """Decode the entire candidate before treating TS discontinuities as recoverable."""

    command = [
        executable,
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        *_decode_timestamp_options(executable),
        "-f",
        "null",
        "-",
    ]
    with tempfile.TemporaryFile() as diagnostics:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=diagnostics,
                timeout=max(60, (duration or 1800) * 2),
            )
        except subprocess.TimeoutExpired as error:
            raise DownloadError("full video validation timed out") from error
        diagnostics.seek(0)
        detail = diagnostics.read(2000).decode("utf-8", errors="replace").strip()
    if result.returncode or detail:
        raise DownloadError(f"full video validation failed ({result.returncode}): {detail}")


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
    executable = find_ffmpeg()
    if executable is None:
        raise DownloadError(
            "FFmpeg is unavailable. Run `uv sync --locked --reinstall-package imageio-ffmpeg` "
            "or install FFmpeg manually; see the GUI tool installation guide."
        )
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
        input_playlist = playlist
        group_playlists: tuple[tuple[Path, float], ...] = ()
        if manifest is not None and manifest.segments is not None:
            batch = download_segments(
                manifest.segments,
                Path(work) / "segments",
                headers={"Referer": episode.page_url},
                cookies=episode.cookies,
                timeout=timeout,
                retries=retries,
            )
            LOGGER.debug("HLS segment evidence: %s", batch.summary)
            input_path = Path(work) / "index.m3u8"
            normalizations = write_local_playlist(input_path, manifest.segments, batch)
            LOGGER.debug(
                "HLS continuity normalization: %s",
                [
                    {
                        "segment": item.segment_index,
                        "offsets": item.offsets,
                        "packets": item.packets_rewritten,
                    }
                    for item in normalizations
                ],
            )
            input_playlist = str(input_path)
            group_playlists = write_group_playlists(input_path, manifest.segments)
        last_error = "FFmpeg produced no video"
        for attempt in range(retries + 1):
            temporary.unlink(missing_ok=True)
            if len(group_playlists) > 1:
                error = _remux_groups(
                    executable,
                    group_playlists,
                    Path(work),
                    temporary,
                    timeout,
                    expected_duration,
                )
            else:
                result, detail, recoverable = _run_ffmpeg(
                    _remux_command(executable, input_playlist, temporary, episode.page_url, timeout)
                )
                error, duration = _remux_error(
                    result,
                    detail,
                    temporary,
                    expected_duration,
                )
                LOGGER.debug(
                    "HLS remux duration expected=%s actual=%s fatal=%s recoverable=%s",
                    f"{expected_duration:.3f}" if expected_duration is not None else "unknown",
                    f"{duration:.3f}" if duration is not None else "unknown",
                    detail or "",
                    recoverable,
                )
            if error is None:
                try:
                    _validate_output(executable, temporary, expected_duration)
                except DownloadError as error:
                    last_error = str(error)
                else:
                    break
            else:
                last_error = error
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
