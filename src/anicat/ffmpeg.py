"""Find a working system or package-provided FFmpeg executable."""

from __future__ import annotations

import shutil
import subprocess


def _works(executable: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.startswith(b"ffmpeg version ")


def find_ffmpeg() -> str | None:
    """Prefer a working system installation, then the binary shipped in the wheel."""

    system = shutil.which("ffmpeg")
    if system and _works(system):
        return system
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        bundled = get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return None
    return bundled if _works(bundled) else None
