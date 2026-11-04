"""AniCat core package."""

import logging
from importlib.metadata import version

from .client import Anime1Client
from .errors import AniCatError, DownloadError, FetchError, ParseError
from .models import DownloadProgressEvent, DownloadResult, Episode, JobReport
from .options import DownloadOptions
from .service import AniCatService

__all__ = [
    "AniCatError",
    "AniCatService",
    "Anime1Client",
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

__version__ = version("anicat")

logging.getLogger(__name__).addHandler(logging.NullHandler())
