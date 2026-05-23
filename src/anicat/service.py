from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol

from .client import Anime1Client
from .downloader import VideoSource, download_episode
from .errors import AniCatError
from .extractor import Anime1Extractor, EpisodeSource
from .models import DownloadProgressEvent, JobReport
from .options import DownloadOptions
from .urls import dedupe, ensure_supported_url, is_episode_url, is_season_url

DownloadProgress = Callable[[DownloadProgressEvent], None]
JobDone = Callable[["JobReport"], None]


class AniCatClient(EpisodeSource, VideoSource, Protocol):
    """Combined client protocol required by extraction and downloading."""

    ...


class AniCatService:
    """Application service that coordinates URL expansion and downloads."""

    def __init__(
        self,
        options: DownloadOptions,
        *,
        client_factory: Callable[[], AniCatClient] | None = None,
    ) -> None:
        self.options = options
        self.client_factory = client_factory or self._default_client

    def collect_episode_urls(self, input_urls: list[str]) -> list[str]:
        """Expand supported input URLs into a de-duplicated episode URL list."""

        client = self.client_factory()
        try:
            extractor = Anime1Extractor(client)
            episode_urls: list[str] = []

            for url in input_urls:
                ensure_supported_url(url)
                if is_season_url(url):
                    episode_urls.extend(extractor.season_episode_urls(url))
                elif is_episode_url(url):
                    episode_urls.append(url)

            return dedupe(episode_urls)
        finally:
            close_client(client)

    def download_many(
        self,
        episode_urls: list[str],
        *,
        on_progress: DownloadProgress | None = None,
        on_done: JobDone | None = None,
    ) -> list[JobReport]:
        """Download multiple episode URLs concurrently and return job reports."""

        reports: list[JobReport] = []

        with ThreadPoolExecutor(max_workers=self.options.worker_count) as executor:
            # Each worker owns its HTTP session to avoid shared cookie/header mutation.
            futures = {
                executor.submit(self.download_one, url, on_progress=on_progress): url
                for url in episode_urls
            }

            for future in as_completed(futures):
                report = future.result()
                reports.append(report)
                if on_done:
                    on_done(report)

        return reports

    def download_one(
        self,
        url: str,
        *,
        on_progress: DownloadProgress | None = None,
    ) -> JobReport:
        """Resolve and download one episode URL, isolating recoverable failures."""

        client = self.client_factory()
        extractor = Anime1Extractor(client)

        try:
            episode = extractor.episode(url)
            result = download_episode(
                client,
                episode,
                self.options.output_dir,
                chunk_size=self.options.safe_chunk_size,
                resume=self.options.resume,
                overwrite=self.options.overwrite,
                progress=on_progress,
            )
            return JobReport(url=url, result=result)
        except AniCatError as error:
            return JobReport(url=url, error=str(error))
        except OSError as error:
            return JobReport(url=url, error=str(error))
        finally:
            close_client(client)

    def _default_client(self) -> AniCatClient:
        """Create the default HTTP client for one worker."""

        return Anime1Client(
            timeout=self.options.request_timeout,
            retries=self.options.retries,
        )


def close_client(client: object) -> None:
    """Close clients that expose a close method without requiring it in tests."""

    close = getattr(client, "close", None)
    if callable(close):
        close()
