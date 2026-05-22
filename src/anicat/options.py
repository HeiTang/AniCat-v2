from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .downloader import DEFAULT_CHUNK_SIZE


@dataclass(frozen=True)
class DownloadOptions:
    """Runtime options shared by CLI and service orchestration."""

    output_dir: Path
    concurrency: int = 3
    timeout: float = 30.0
    retries: int = 3
    chunk_size: int = DEFAULT_CHUNK_SIZE
    resume: bool = True
    overwrite: bool = False
    progress: bool = True

    @property
    def worker_count(self) -> int:
        """Return a safe worker count for ThreadPoolExecutor."""

        return max(1, self.concurrency)

    @property
    def safe_chunk_size(self) -> int:
        """Return a minimum chunk size to avoid inefficient tiny reads."""

        return max(1024, self.chunk_size)

    @property
    def request_timeout(self) -> tuple[float, float]:
        """Return connect/read timeout tuple used by requests."""

        return (10.0, self.timeout)
