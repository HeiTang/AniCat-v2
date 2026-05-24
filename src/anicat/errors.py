class AniCatError(Exception):
    """Base error for recoverable AniCat failures."""


class FetchError(AniCatError):
    """HTTP/network failure."""


class ParseError(AniCatError):
    """Unexpected upstream HTML/API shape."""


class DownloadError(AniCatError):
    """Download/write failure."""
