import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from anicat.models import DownloadResult
from anicat.options import DownloadOptions
from anicat.service import AniCatService


class HlsServiceTests(unittest.TestCase):
    @patch("anicat.service.download_episode")
    @patch("anicat.service.download_hls_episode")
    def test_hls_urls_reach_hls_backend_not_direct_downloader(self, hls, direct):
        for url in (
            "https://anime1.cc/681899930-01-0142",
            "https://anime1.in/2023-xian-ni-10142000",
        ):
            with self.subTest(url=url), TemporaryDirectory() as directory:
                client = Mock()
                client.get_page.return_value = (
                    '<h1 class="entry-title">show</h1>'
                    '<iframe src="/_player?url=https://cdn.example/index.m3u8"></iframe>'
                )
                hls.side_effect = lambda episode, output, **kwargs: DownloadResult(
                    episode, output / "show.mp4", "downloaded", 42, 42
                )
                service = AniCatService(
                    DownloadOptions(Path(directory)), client_factory=Mock(return_value=client)
                )
                self.assertEqual(service.collect_episode_urls([url]), [url])
                report = service.download_one(url)
                self.assertIsNone(report.error)
                self.assertIsNotNone(report.result)
                self.assertEqual(hls.call_args.args[0].stream_type, "hls")
                direct.assert_not_called()

    @patch("anicat.hls_downloader.shutil.which", return_value=None)
    def test_missing_ffmpeg_is_reported_as_recoverable_failure(self, _which):
        client = Mock()
        client.get_page.return_value = (
            '<h1 class="entry-title">show</h1>'
            '<iframe src="/_player?url=https://cdn.example/index.m3u8"></iframe>'
        )
        with TemporaryDirectory() as directory:
            service = AniCatService(
                DownloadOptions(Path(directory)), client_factory=Mock(return_value=client)
            )
            report = service.download_one("https://anime1.cc/681899930-01-0142")
            self.assertIn("require FFmpeg", report.error or "")
            self.assertIsNone(report.result)
