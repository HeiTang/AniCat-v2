from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRIES,
)


@dataclass(frozen=True)
class DownloadOptions:
    """Runtime options shared by CLI and service orchestration."""

    output_dir: Path
    concurrency: int = DEFAULT_CONCURRENCY
    timeout: float = DEFAULT_READ_TIMEOUT
    retries: int = DEFAULT_RETRIES
    chunk_size: int = DEFAULT_CHUNK_SIZE
    resume: bool = True
    overwrite: bool = False
    progress: bool = True

    def __post_init__(self) -> None:
        """Validate runtime options early instead of silently coercing bad values."""

        if self.concurrency < 1:
            raise ValueError("concurrency must be greater than or equal to 1")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if self.retries < 0:
            raise ValueError("retries must be greater than or equal to 0")
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")

    @property
    def worker_count(self) -> int:
        """Return a safe worker count for ThreadPoolExecutor."""

        return self.concurrency

    @property
    def safe_chunk_size(self) -> int:
        """Return the validated chunk size used by streaming downloads."""

        return self.chunk_size

    @property
    def request_timeout(self) -> tuple[float, float]:
        """Return connect/read timeout tuple used by requests."""

        return (DEFAULT_CONNECT_TIMEOUT, self.timeout)
