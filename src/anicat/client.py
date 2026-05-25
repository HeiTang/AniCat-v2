from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, cast

import requests

from .constants import (
    API_URL,
    DEFAULT_BACKOFF,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRIES,
    RETRY_STATUS_CODES,
)
from .errors import FetchError
from .models import VideoStreamResponse

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "DNT": "1",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

LOGGER = logging.getLogger(__name__)


class Anime1Client:
    """HTTP client wrapper for Anime1 page, API, and video requests."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float | tuple[float, float] = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}

    def __enter__(self) -> Anime1Client:
        """Return this client so callers can manage it with a context manager."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying HTTP session when leaving a context manager."""

        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session and release pooled connections."""

        self.session.close()

    def post_page(self, url: str) -> str:
        """Fetch an Anime1 HTML page using the upstream-expected POST method."""

        return self.request("POST", url).text

    def get_page(self, url: str) -> str:
        """Fetch an Anime1 HTML page using a normal browser-style GET method."""

        return self.request("GET", url).text

    def post_api(self, data_apireq: str) -> requests.Response:
        """Submit episode API payload and return the raw API response."""

        # Anime1 signs the already URL-encoded payload; passing a dict would
        # double-encode it and make the upstream API reject the signature.
        return self.request(
            "POST",
            API_URL,
            data=f"d={data_apireq}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def stream_video(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> VideoStreamResponse:
        """Open a streaming response for an episode video file."""

        request_headers = {
            "Accept-Encoding": "identity;q=1, *;q=0",
            **(headers or {}),
        }
        return cast(
            VideoStreamResponse,
            self.request(
                "GET",
                url,
                headers=request_headers,
                cookies=dict(cookies),
                stream=True,
            ),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Send an HTTP request with retry/backoff and normalized errors."""

        request_headers = {**self.headers, **(headers or {})}
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            attempt_number = attempt + 1
            try:
                LOGGER.debug(
                    "HTTP %s %s attempt %d/%d",
                    method,
                    url,
                    attempt_number,
                    self.retries + 1,
                )
                response = self.session.request(
                    method,
                    url,
                    headers=request_headers,
                    timeout=timeout if timeout is not None else self.timeout,
                    **kwargs,
                )
                # Retry only transient upstream/server throttling errors.
                if response.status_code in RETRY_STATUS_CODES and attempt < self.retries:
                    response.close()
                    LOGGER.warning(
                        "HTTP %s %s returned %d; retrying attempt %d/%d",
                        method,
                        url,
                        response.status_code,
                        attempt_number + 1,
                        self.retries + 1,
                    )
                    self._sleep(attempt)
                    continue
                response.raise_for_status()
                LOGGER.debug("HTTP %s %s completed with %d", method, url, response.status_code)
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt >= self.retries:
                    break
                LOGGER.warning(
                    "HTTP %s %s failed: %s; retrying attempt %d/%d",
                    method,
                    url,
                    error,
                    attempt_number + 1,
                    self.retries + 1,
                )
                self._sleep(attempt)

        LOGGER.debug("HTTP %s %s exhausted retries: %s", method, url, last_error)
        raise FetchError(f"{method} {url} failed: {last_error}") from last_error

    def _sleep(self, attempt: int) -> None:
        """Sleep for exponential backoff between retry attempts."""

        if self.backoff <= 0:
            return
        time.sleep(self.backoff * (2**attempt))
