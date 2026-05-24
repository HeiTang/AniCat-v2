import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar, NoReturn, cast

import requests

from anicat.models import VideoStreamResponse
from anicat.options import DownloadOptions
from anicat.service import AniCatService


class BadClient:
    def post_page(self, url: str) -> str:
        return "<html></html>"

    def post_api(self, data_apireq: str) -> NoReturn:
        raise AssertionError("post_api should not be called")

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> NoReturn:
        raise AssertionError("stream_video should not be called")


class ApiResponse:
    text = "{}"
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self) -> None:
        self.cookies = {
            "e": "1",
            "p": "2",
            "h": "3",
        }

    def json(self) -> dict[str, dict[str, str]]:
        return {"s": {"src": "//cdn.example/demo.mp4"}}


class VideoResponse:
    status_code = 200
    content = b"a" * 2500

    def __init__(self) -> None:
        self.headers = {"content-length": str(len(self.content))}
        self.closed = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]

    def close(self) -> None:
        self.closed = True


class GoodClient:
    instances: ClassVar[list["GoodClient"]] = []

    def __init__(self) -> None:
        self.closed = False
        self.instances.append(self)

    def post_page(self, url: str) -> str:
        return """
        <h2 class="entry-title">Demo</h2>
        <video class="video-js" data-apireq="%7B%7D"></video>
        """

    def post_api(self, data_apireq: str) -> requests.Response:
        return cast(requests.Response, ApiResponse())

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> VideoStreamResponse:
        return VideoResponse()

    def close(self) -> None:
        self.closed = True


class ServiceTests(unittest.TestCase):
    def test_download_one_reports_recoverable_extractor_error(self):
        service = AniCatService(
            DownloadOptions(output_dir=Path("unused")),
            client_factory=BadClient,
        )

        report = service.download_one("https://anime1.me/1")

        self.assertEqual(report.url, "https://anime1.me/1")
        self.assertIsNone(report.result)
        error = report.error
        assert error is not None
        self.assertIn("data-apireq", error)

    def test_download_many_reports_chunk_progress(self):
        progress: list[tuple[str, int, int, int | None]] = []
        GoodClient.instances.clear()

        with TemporaryDirectory() as directory:
            service = AniCatService(
                DownloadOptions(
                    output_dir=Path(directory),
                    concurrency=1,
                    chunk_size=1024,
                ),
                client_factory=GoodClient,
            )

            reports = service.download_many(
                ["https://anime1.me/1"],
                on_progress=lambda event: progress.append(
                    (
                        event.phase,
                        event.bytes_delta,
                        event.bytes_completed,
                        event.total_bytes,
                    )
                ),
            )

            self.assertEqual(
                progress,
                [
                    ("started", 0, 0, 2500),
                    ("advanced", 1024, 1024, 2500),
                    ("advanced", 1024, 2048, 2500),
                    ("advanced", 452, 2500, 2500),
                ],
            )
            self.assertEqual(len(reports), 1)
            result = reports[0].result
            assert result is not None
            self.assertEqual(result.path.read_bytes(), VideoResponse.content)
            self.assertEqual(len(GoodClient.instances), 1)
            self.assertTrue(GoodClient.instances[0].closed)

    def test_download_many_reuses_one_client_per_worker_thread(self):
        GoodClient.instances.clear()

        with TemporaryDirectory() as directory:
            service = AniCatService(
                DownloadOptions(
                    output_dir=Path(directory),
                    concurrency=1,
                    chunk_size=1024,
                ),
                client_factory=GoodClient,
            )

            reports = service.download_many(
                [
                    "https://anime1.me/1",
                    "https://anime1.me/2",
                ]
            )

            self.assertEqual(len(reports), 2)
            self.assertEqual(len(GoodClient.instances), 1)
            self.assertTrue(GoodClient.instances[0].closed)

    def test_collect_episode_urls_closes_client(self):
        GoodClient.instances.clear()

        service = AniCatService(
            DownloadOptions(output_dir=Path("unused")),
            client_factory=GoodClient,
        )

        urls = service.collect_episode_urls(["https://anime1.me/1"])

        self.assertEqual(urls, ["https://anime1.me/1"])
        self.assertEqual(len(GoodClient.instances), 1)
        self.assertTrue(GoodClient.instances[0].closed)


if __name__ == "__main__":
    unittest.main()
