from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .constants import PROGRESS_TITLE_MAX_LENGTH
from .models import DownloadProgressEvent, JobReport


@dataclass(frozen=True)
class ProgressCallbacks:
    """Callbacks passed from CLI UI into the service layer."""

    on_progress: Callable[[DownloadProgressEvent], None]
    on_done: Callable[[JobReport], None]


@contextmanager
def rich_download_progress(total_jobs: int) -> Iterator[ProgressCallbacks]:
    """Render overall and active per-file progress bars with Rich."""

    # Worker threads report progress concurrently; Rich updates must stay serialized.
    lock = Lock()
    active_tasks: dict[str, TaskID] = {}
    completed_jobs = 0
    failed_jobs = 0
    skipped_jobs = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        # Overall size is unknown until every episode has resolved its stream response.
        overall_task = progress.add_task(
            overall_description(completed_jobs, total_jobs, failed_jobs, skipped_jobs),
            total=None,
        )

        def on_progress(event: DownloadProgressEvent) -> None:
            """Update overall and per-file progress for one downloader event."""

            key = event.episode.page_url
            with lock:
                task_id = active_tasks.get(key)
                if task_id is None:
                    # Create per-file tasks lazily when the stream headers are known.
                    task_id = progress.add_task(
                        trim_title(event.episode.title),
                        total=event.total_bytes,
                        completed=event.bytes_completed,
                    )
                    active_tasks[key] = task_id
                else:
                    progress.update(
                        task_id,
                        total=event.total_bytes,
                        completed=event.bytes_completed,
                    )

                if event.phase == "reset":
                    # Reset only affects the current file bar; cumulative transfer never rewinds.
                    return

                if event.bytes_delta:
                    progress.update(overall_task, advance=event.bytes_delta)

        def on_done(report: JobReport) -> None:
            """Update job counters and remove the finished per-file task."""

            nonlocal completed_jobs, failed_jobs, skipped_jobs

            key = report.result.episode.page_url if report.result else report.url
            with lock:
                completed_jobs += 1
                if report.error:
                    failed_jobs += 1
                elif report.result and report.result.status == "skipped":
                    skipped_jobs += 1

                task_id = active_tasks.pop(key, None)
                if task_id is not None:
                    # Keep the display focused on active downloads instead of completed rows.
                    progress.remove_task(task_id)

                progress.update(
                    overall_task,
                    description=overall_description(
                        completed_jobs,
                        total_jobs,
                        failed_jobs,
                        skipped_jobs,
                    ),
                )

        yield ProgressCallbacks(on_progress=on_progress, on_done=on_done)


def overall_description(
    completed_jobs: int,
    total_jobs: int,
    failed_jobs: int,
    skipped_jobs: int,
) -> str:
    """Build the overall progress task label."""

    parts = [f"Overall {completed_jobs}/{total_jobs} episodes"]
    if failed_jobs:
        parts.append(f"{failed_jobs} failed")
    if skipped_jobs:
        parts.append(f"{skipped_jobs} skipped")
    return " · ".join(parts)


def trim_title(title: str, *, max_length: int = PROGRESS_TITLE_MAX_LENGTH) -> str:
    """Normalize and truncate episode titles for compact terminal display."""

    normalized = " ".join(title.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}…"
