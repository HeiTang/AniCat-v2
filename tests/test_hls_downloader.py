import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anicat.errors import DownloadError
from anicat.hls_downloader import download_hls_episode
from anicat.models import Episode


class HlsDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.episode = Episode("https://anime1.cc/show", "show", "https://cdn.example/a.m3u8")

    @patch("anicat.hls_downloader.shutil.which", return_value=None)
    def test_missing_ffmpeg(self, _which):
        with self.assertRaisesRegex(DownloadError, "require FFmpeg"):
            download_hls_episode(self.episode, self.output)

    @patch("anicat.hls_downloader.shutil.which", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_failure_preserves_existing_output_and_cleans_temporary(self, run, _which):
        path = self.output / "show.mp4"
        path.write_bytes(b"old")
        run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(DownloadError, "FFmpeg failed"):
            download_hls_episode(self.episode, self.output, overwrite=True)
        self.assertEqual(path.read_bytes(), b"old")
        self.assertEqual(list(self.output.iterdir()), [path])

    @patch("anicat.hls_downloader.shutil.which", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_success_replaces_output_only_when_complete(self, run, _which):
        def finish(command, **kwargs):
            Path(command[-1]).write_bytes(b"video")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = finish
        result = download_hls_episode(self.episode, self.output)
        self.assertEqual(result.path.read_bytes(), b"video")
        self.assertEqual(result.bytes_written, 5)
        self.assertEqual(list(self.output.iterdir()), [result.path])

    @patch("anicat.hls_downloader.subprocess.run")
    def test_existing_output_skips_without_ffmpeg(self, run):
        (self.output / "show.mp4").write_bytes(b"old")
        self.assertEqual(download_hls_episode(self.episode, self.output).status, "skipped")
        run.assert_not_called()

    @patch("anicat.hls_downloader.shutil.which", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_empty_success_is_rejected(self, run, _which):
        run.return_value = subprocess.CompletedProcess([], 0)
        with self.assertRaisesRegex(DownloadError, "no video"):
            download_hls_episode(self.episode, self.output)
        self.assertEqual(list(self.output.iterdir()), [])
