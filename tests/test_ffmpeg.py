import os
import subprocess
import unittest
from unittest.mock import patch

from anicat.ffmpeg import find_ffmpeg


class FfmpegTests(unittest.TestCase):
    @patch("imageio_ffmpeg.get_ffmpeg_exe")
    @patch("anicat.ffmpeg.shutil.which", return_value="/system/ffmpeg")
    @patch("anicat.ffmpeg.subprocess.run")
    def test_working_system_installation_is_preferred(self, run, _which, bundled):
        run.return_value = subprocess.CompletedProcess([], 0, stdout=b"ffmpeg version 8.0\n")
        self.assertEqual(find_ffmpeg(), "/system/ffmpeg")
        bundled.assert_not_called()

    @patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/bundled/ffmpeg")
    @patch("anicat.ffmpeg.shutil.which", return_value="/broken/ffmpeg")
    @patch("anicat.ffmpeg.subprocess.run")
    def test_broken_system_installation_falls_back_to_package(self, run, _which, _bundled):
        run.side_effect = [
            OSError("cannot execute"),
            subprocess.CompletedProcess([], 0, stdout=b"ffmpeg version 8.0\n"),
        ]
        self.assertEqual(find_ffmpeg(), "/bundled/ffmpeg")

    @patch("imageio_ffmpeg.get_ffmpeg_exe", side_effect=RuntimeError("missing"))
    @patch("anicat.ffmpeg.shutil.which", return_value=None)
    def test_missing_executable(self, _which, _bundled):
        self.assertIsNone(find_ffmpeg())

    @patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/broken/ffmpeg")
    @patch("anicat.ffmpeg.shutil.which", return_value=None)
    @patch("anicat.ffmpeg.subprocess.run")
    def test_hung_or_unrelated_executable_is_not_ready(self, run, _which, _bundled):
        for result in (
            subprocess.TimeoutExpired("ffmpeg", 5),
            subprocess.CompletedProcess([], 0, stdout=b"different program\n"),
            subprocess.CompletedProcess([], 1, stdout=b"ffmpeg version 8.0\n"),
        ):
            with self.subTest(result=result):
                run.side_effect = result if isinstance(result, Exception) else None
                run.return_value = result
                self.assertIsNone(find_ffmpeg())

    @patch.dict(os.environ, {"PATH": "", "IMAGEIO_FFMPEG_EXE": ""})
    def test_packaged_binary_runs_without_system_path(self):
        executable = find_ffmpeg()
        self.assertIsNotNone(executable)
        assert executable is not None
        result = subprocess.run([executable, "-version"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith(b"ffmpeg version "))
