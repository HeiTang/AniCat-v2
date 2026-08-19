"""AniCat core package."""

import logging

from .client import Anime1Client
from .errors import AniCatError, DownloadError, FetchError, ParseError
from .models import AnimeEntry, DownloadProgressEvent, DownloadResult, Episode, JobReport
from .options import DownloadOptions
from .service import AniCatService

__all__ = [
    "AniCatError",
    "AniCatService",
    "Anime1Client",
    "AnimeEntry",
    "DownloadError",
    "DownloadOptions",
    "DownloadProgressEvent",
    "DownloadResult",
    "Episode",
    "FetchError",
    "JobReport",
    "ParseError",
    "__version__",
]

__version__ = "0.2.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())
