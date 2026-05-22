import unittest
from typing import Any, cast

from anicat.client import API_URL, Anime1Client


class FakeResponse:
    status_code = 200
    text = "{}"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return FakeResponse()


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


if __name__ == "__main__":
    unittest.main()
