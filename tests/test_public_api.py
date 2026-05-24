import importlib.resources
import unittest

import anicat
from anicat import (
    AniCatError,
    AniCatService,
    Anime1Client,
    DownloadError,
    DownloadOptions,
    DownloadProgressEvent,
    DownloadResult,
    Episode,
    FetchError,
    JobReport,
    ParseError,
)


class PublicApiTests(unittest.TestCase):
    def test_root_package_re_exports_stable_api(self):
        self.assertIs(anicat.AniCatError, AniCatError)
        self.assertIs(anicat.AniCatService, AniCatService)
        self.assertIs(anicat.Anime1Client, Anime1Client)
        self.assertIs(anicat.DownloadError, DownloadError)
        self.assertIs(anicat.DownloadOptions, DownloadOptions)
        self.assertIs(anicat.DownloadProgressEvent, DownloadProgressEvent)
        self.assertIs(anicat.DownloadResult, DownloadResult)
        self.assertIs(anicat.Episode, Episode)
        self.assertIs(anicat.FetchError, FetchError)
        self.assertIs(anicat.JobReport, JobReport)
        self.assertIs(anicat.ParseError, ParseError)

    def test_package_declares_typed_marker(self):
        marker = importlib.resources.files("anicat").joinpath("py.typed")

        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
