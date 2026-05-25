import unittest
from typing import Any, ClassVar, cast

from anicat.client import API_URL, Anime1Client


class FakeResponse:
    status_code = 200
    text = "{}"
    headers: ClassVar[dict[str, str]] = {}

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return FakeResponse()

    def close(self) -> None:
        self.closed = True


class ClientTests(unittest.TestCase):
    def test_post_api_sends_raw_form_body_without_double_encoding(self):
        session = FakeSession()
        client = Anime1Client(session=cast(Any, session))

        client.post_api("%7B%22c%22%3A%221846%22%7D")

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, API_URL)
        self.assertEqual(kwargs["data"], "d=%7B%22c%22%3A%221846%22%7D")
        self.assertEqual(
            kwargs["headers"]["Content-Type"],
            "application/x-www-form-urlencoded",
        )

    def test_get_page_uses_get_method(self):
        session = FakeSession()
        client = Anime1Client(session=cast(Any, session))

        client.get_page("https://anime1.pw/349")

        method, url, _ = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://anime1.pw/349")

    def test_stream_video_uses_client_timeout(self):
        session = FakeSession()
        client = Anime1Client(session=cast(Any, session), timeout=(1.0, 2.0))

        client.stream_video("https://cdn.example/demo.mp4", cookies={})

        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["timeout"], (1.0, 2.0))

    def test_request_honors_explicit_falsy_timeout(self):
        session = FakeSession()
        client = Anime1Client(session=cast(Any, session), timeout=(1.0, 2.0))

        client.request("GET", "https://anime1.me/1", timeout=0)

        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["timeout"], 0)

    def test_close_releases_session(self):
        session = FakeSession()

        with Anime1Client(session=cast(Any, session)):
            pass

        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
