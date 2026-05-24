import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from anicat.downloader import download_episode, sanitize_filename
from anicat.errors import DownloadError
from anicat.models import Episode


class FakeResponse:
    def __init__(
        self, content: bytes, *, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]

    def close(self) -> None:
        self.closed = True


class BrokenResponse(FakeResponse):
    def __init__(
        self,
        content: bytes,
        *,
        error: requests.RequestException,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers)
        self.error = error

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        yield from super().iter_content(chunk_size)
        raise self.error


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append(dict(headers or {}))
        return self.response


class SequentialClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append(dict(headers or {}))
        return self.responses.pop(0)


class DownloaderTests(unittest.TestCase):
    def test_sanitize_filename_removes_unsafe_characters(self):
        self.assertEqual(sanitize_filename(' A/B:C* "Demo" '), "A_B_C_ _Demo_")
        self.assertEqual(sanitize_filename("CON"), "episode")
        self.assertEqual(sanitize_filename(""), "episode")

    def test_download_writes_part_then_renames(self):
        response = FakeResponse(b"abcdef", headers={"content-length": "6"})
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={"e": "1", "p": "2", "h": "3"},
        )

        with TemporaryDirectory() as directory:
            result = download_episode(client, episode, Path(directory), chunk_size=2)
            self.assertEqual(result.status, "downloaded")
            self.assertEqual(result.bytes_written, 6)
            self.assertEqual(result.path.read_bytes(), b"abcdef")
            self.assertFalse(result.path.with_name("Demo.mp4.part").exists())
            self.assertTrue(response.closed)

    def test_download_skips_existing_file_without_http_request(self):
        response = FakeResponse(b"unused", headers={"content-length": "6"})
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "Demo.mp4"
            path.write_bytes(b"existing")
            result = download_episode(client, episode, Path(directory))

            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.bytes_written, len(b"existing"))
            self.assertEqual(client.calls, [])

    def test_download_resumes_partial_file(self):
        response = FakeResponse(
            b"def",
            status_code=206,
            headers={"content-range": "bytes 3-5/6"},
        )
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            part_path = Path(directory) / "Demo.mp4.part"
            part_path.write_bytes(b"abc")

            result = download_episode(client, episode, Path(directory), chunk_size=2)

            self.assertEqual(result.path.read_bytes(), b"abcdef")
            self.assertEqual(client.calls, [{"Range": "bytes=3-"}])

    def test_download_restarts_when_server_ignores_range(self):
        response = FakeResponse(b"abcdef", headers={"content-length": "6"})
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            part_path = Path(directory) / "Demo.mp4.part"
            part_path.write_bytes(b"abc")

            result = download_episode(client, episode, Path(directory), chunk_size=2)

            self.assertEqual(result.path.read_bytes(), b"abcdef")
            self.assertEqual(client.calls, [{"Range": "bytes=3-"}])

    def test_download_emits_reset_progress_when_server_ignores_range(self):
        progress: list[tuple[str, int, int, int | None]] = []
        response = FakeResponse(b"abcdef", headers={"content-length": "6"})
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            part_path = Path(directory) / "Demo.mp4.part"
            part_path.write_bytes(b"abc")

            download_episode(
                client,
                episode,
                Path(directory),
                chunk_size=3,
                progress=lambda event: progress.append(
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
                    ("reset", 0, 0, None),
                    ("started", 0, 0, 6),
                    ("advanced", 3, 3, 6),
                    ("advanced", 3, 6, 6),
                ],
            )

    def test_download_retries_interrupted_stream_from_partial_size(self):
        first_response = BrokenResponse(
            b"abc",
            error=requests.exceptions.ChunkedEncodingError("connection dropped"),
            headers={"content-length": "6", "ETag": '"demo-etag"'},
        )
        second_response = FakeResponse(
            b"def",
            status_code=206,
            headers={"content-range": "bytes 3-5/6"},
        )
        client = SequentialClient([first_response, second_response])
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            result = download_episode(
                client,
                episode,
                Path(directory),
                chunk_size=3,
                stream_retries=1,
            )

            self.assertEqual(result.path.read_bytes(), b"abcdef")
            self.assertEqual(client.calls, [{}, {"Range": "bytes=3-", "If-Range": '"demo-etag"'}])
            self.assertTrue(first_response.closed)
            self.assertTrue(second_response.closed)

    def test_download_rejects_mismatched_content_range_start(self):
        response = FakeResponse(
            b"ef",
            status_code=206,
            headers={"content-range": "bytes 4-5/6"},
        )
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            part_path = Path(directory) / "Demo.mp4.part"
            part_path.write_bytes(b"abc")

            with self.assertRaisesRegex(DownloadError, "starts at 4, expected 3"):
                download_episode(
                    client,
                    episode,
                    Path(directory),
                    chunk_size=2,
                    stream_retries=0,
                )

            self.assertEqual(part_path.read_bytes(), b"abc")
            self.assertEqual(client.calls, [{"Range": "bytes=3-"}])

    def test_download_rejects_changed_total_size_between_attempts(self):
        first_response = BrokenResponse(
            b"abc",
            error=requests.exceptions.ChunkedEncodingError("connection dropped"),
            headers={"content-length": "6"},
        )
        second_response = FakeResponse(
            b"def",
            status_code=206,
            headers={"content-range": "bytes 3-5/7"},
        )
        client = SequentialClient([first_response, second_response])
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DownloadError, "stream size changed"):
                download_episode(
                    client,
                    episode,
                    Path(directory),
                    chunk_size=3,
                    stream_retries=1,
                )

            self.assertEqual((Path(directory) / "Demo.mp4.part").read_bytes(), b"abc")

    def test_incomplete_download_keeps_part_file(self):
        response = FakeResponse(b"abc", headers={"content-length": "5"})
        client = FakeClient(response)
        episode = Episode(
            page_url="https://anime1.me/1",
            title="Demo",
            stream_url="https://cdn.example/demo.mp4",
            cookies={},
        )

        with TemporaryDirectory() as directory:
            with self.assertRaises(DownloadError):
                download_episode(client, episode, Path(directory), chunk_size=2)

            self.assertEqual((Path(directory) / "Demo.mp4.part").read_bytes(), b"abc")


if __name__ == "__main__":
    unittest.main()
