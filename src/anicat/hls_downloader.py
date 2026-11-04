"""Download HLS with FFmpeg while keeping completed files intact on failure."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from .downloader import ProgressCallback, target_path
from .errors import DownloadError
from .hls import validate_playlist_url
from .models import DownloadProgressEvent, DownloadResult, Episode


def download_hls_episode(
    episode: Episode,
    output_dir: Path,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
    timeout: float = 30,
) -> DownloadResult:
    """Remux a playlist to MP4; failed HLS attempts restart from the beginning."""

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
    if any(character in episode.page_url for character in "\r\n"):
        raise DownloadError("invalid episode Referer")
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
        # Keep FFmpeg diagnostics on disk so long failures cannot exhaust memory.
        with tempfile.TemporaryFile() as diagnostics:
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=diagnostics)
            except OSError as error:
                raise DownloadError(f"could not start FFmpeg: {error}") from error
            if result.returncode:
                diagnostics.seek(0, 2)
                diagnostics.seek(max(0, diagnostics.tell() - 2000))
                detail = diagnostics.read().decode("utf-8", errors="replace").strip()
                raise DownloadError(f"FFmpeg failed ({result.returncode}): {detail}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise DownloadError("FFmpeg produced no video")
        size = temporary.stat().st_size
        temporary.replace(path)
    # shortcut: report final bytes only; use FFmpeg -progress for live HLS byte progress.
    if progress:
        progress(DownloadProgressEvent(episode, "advanced", size, size, size))
    return DownloadResult(episode, path, "downloaded", size, size)
