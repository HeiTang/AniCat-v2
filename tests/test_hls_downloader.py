import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anicat.errors import DownloadError
from anicat.hls_downloader import HlsManifest, _decode_timestamp_options, download_hls_episode
from anicat.models import Episode


class HlsDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.episode = Episode("https://anime1.cc/show", "show", "https://cdn.example/a.m3u8")
        validation = patch("anicat.hls_downloader._validate_hls_sources", return_value=None)
        validation.start()
        self.addCleanup(validation.stop)
        output_validation = patch("anicat.hls_downloader._validate_output")
        output_validation.start()
        self.addCleanup(output_validation.stop)

    @patch("anicat.hls_downloader.find_ffmpeg", return_value=None)
    def test_missing_ffmpeg(self, _which):
        with self.assertRaisesRegex(DownloadError, "FFmpeg is unavailable"):
            download_hls_episode(self.episode, self.output)

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_failure_preserves_existing_output_and_cleans_temporary(self, run, _which):
        path = self.output / "show.mp4"
        path.write_bytes(b"old")
        run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(DownloadError, "FFmpeg failed.*after 1 attempt"):
            download_hls_episode(self.episode, self.output, overwrite=True, retries=0)
        self.assertEqual(path.read_bytes(), b"old")
        self.assertEqual(list(self.output.iterdir()), [path])

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
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

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_empty_success_is_rejected(self, run, _which):
        run.return_value = subprocess.CompletedProcess([], 0)
        with self.assertRaisesRegex(DownloadError, "no video.*after 4 attempt"):
            download_hls_episode(self.episode, self.output, retries=3)
        self.assertEqual(list(self.output.iterdir()), [])

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_failed_session_is_retried_and_replaces_only_after_success(self, run, _which):
        def finish(command, **kwargs):
            if run.call_count == 2:
                Path(command[-1]).write_bytes(b"video")
                return subprocess.CompletedProcess(command, 0)
            return subprocess.CompletedProcess(command, 1)

        run.side_effect = finish
        result = download_hls_episode(self.episode, self.output, retries=1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result.path.read_bytes(), b"video")

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_exhausted_retries_preserve_existing_output(self, run, _which):
        path = self.output / "show.mp4"
        path.write_bytes(b"old")
        run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(DownloadError, "after 2 attempt"):
            download_hls_episode(self.episode, self.output, overwrite=True, retries=1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(path.read_bytes(), b"old")

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    def test_negative_retries_are_rejected(self, _which):
        with self.assertRaisesRegex(ValueError, "retries"):
            download_hls_episode(self.episode, self.output, retries=-1)

    @patch("anicat.hls_downloader._validate_hls_sources")
    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_preflight_timeout_is_retried(self, run, _which, validate):
        validate.side_effect = [
            DownloadError("could not fetch HLS segment: timed out"),
            HlsManifest("https://cdn.example/index.m3u8", 3, 2),
        ]

        def finish(command, **kwargs):
            Path(command[-1]).write_bytes(b"video")
            return subprocess.CompletedProcess(command, 0, stdout=b"out_time_us=2000000\n")

        run.side_effect = finish
        result = download_hls_episode(self.episode, self.output, retries=1, timeout=1)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result.path.read_bytes(), b"video")

    @patch(
        "anicat.hls_downloader._validate_hls_sources",
        return_value=HlsManifest("https://cdn.example/index.m3u8", 3, 2),
    )
    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_duration_mismatch_is_rejected(self, run, _which, _validate):
        def finish(command, **kwargs):
            Path(command[-1]).write_bytes(b"video")
            return subprocess.CompletedProcess(command, 0, stdout=b"out_time_us=1000000\n")

        run.side_effect = finish
        with self.assertRaisesRegex(DownloadError, "duration mismatch"):
            download_hls_episode(self.episode, self.output, retries=0)
        self.assertFalse((self.output / "show.mp4").exists())

    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_zero_exit_with_errors_preserves_existing_file(self, run, _which):
        path = self.output / "show.mp4"
        path.write_bytes(b"existing")

        def finish(command, **kwargs):
            Path(command[-1]).write_bytes(b"damaged")
            kwargs["stderr"].write(b"Error when loading segment 2")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = finish
        with self.assertRaisesRegex(DownloadError, "Error when loading segment"):
            download_hls_episode(self.episode, self.output, overwrite=True, retries=0)
        self.assertEqual(path.read_bytes(), b"existing")

    @patch("anicat.hls_downloader._validate_output", side_effect=DownloadError("corrupt frame"))
    @patch("anicat.hls_downloader.find_ffmpeg", return_value="/bin/ffmpeg")
    @patch("anicat.hls_downloader.subprocess.run")
    def test_full_decode_failure_preserves_completed_file(self, run, _which, validate):
        path = self.output / "show.mp4"
        path.write_bytes(b"original")

        def finish(command, **kwargs):
            Path(command[-1]).write_bytes(b"damaged")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = finish
        with self.assertRaisesRegex(DownloadError, "corrupt frame"):
            download_hls_episode(self.episode, self.output, overwrite=True, retries=0)
        self.assertEqual(path.read_bytes(), b"original")


class FfmpegCompatibilityTests(unittest.TestCase):
    @patch("anicat.hls_downloader.subprocess.run")
    def test_decode_timestamp_options_follow_ffmpeg_major(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout=b"ffmpeg version 4.4 Copyright (c) 2000-2021"
        )
        self.assertEqual(
            _decode_timestamp_options("/old/ffmpeg"),
            ("-vsync", "0", "-enc_time_base", "-1"),
        )
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout=b"ffmpeg version n9.0.2-17-g2a571b6068-20260930"
        )
        self.assertEqual(
            _decode_timestamp_options("/new/ffmpeg"),
            ("-fps_mode", "passthrough", "-enc_time_base", "demux"),
        )
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=b"ffmpeg version N-nightly\nlibavcodec 63.0.0\n",
        )
        self.assertEqual(
            _decode_timestamp_options("/nightly/ffmpeg"),
            ("-fps_mode", "passthrough", "-enc_time_base", "demux"),
        )


class AudioTimestampDiagnosticTests(unittest.TestCase):
    def test_only_small_audio_mux_correction_is_allowed(self):
        from anicat.hls_downloader import _safe_audio_timestamp_correction

        prefix = "[mp4] Non-monotonous DTS in output stream "
        self.assertTrue(
            _safe_audio_timestamp_correction(
                prefix + "0:1; previous: 18032661, current: 18030288; changing to 18032662."
            )
        )
        self.assertTrue(
            _safe_audio_timestamp_correction(
                "[aost#0:1/copy @ 0x123] Non-monotonic DTS; "
                "previous: 98304, current: 98064; changing to 98305. "
                "This may result in incorrect timestamps in the output file."
            )
        )
        self.assertFalse(
            _safe_audio_timestamp_correction(
                prefix + "0:1; previous: 18032661, current: 0; changing to 18032662."
            )
        )
        self.assertFalse(
            _safe_audio_timestamp_correction(
                "[aost#0:1/copy @ 0x123] Non-monotonic DTS; "
                "previous: 98304, current: 90000; changing to 98305."
            )
        )
        self.assertFalse(
            _safe_audio_timestamp_correction(
                "[vost#0:0/copy @ 0x123] Non-monotonic DTS; "
                "previous: 98304, current: 98064; changing to 98305."
            )
        )
        self.assertFalse(
            _safe_audio_timestamp_correction(
                "[aost#0:1 @ 0x123] Non-monotonic DTS; "
                "previous: 98304, current: 98064; changing to 98305."
            )
        )
        self.assertFalse(
            _safe_audio_timestamp_correction(
                prefix + "0:0; previous: 20, current: 19; changing to 21."
            )
        )
        self.assertFalse(_safe_audio_timestamp_correction("Failed to open segment 2"))

    def test_audio_queue_backward_warning_is_fatal(self):
        from anicat.hls_downloader import _classify_ffmpeg_diagnostic

        self.assertEqual(
            _classify_ffmpeg_diagnostic("[aac] Queue input is backward in time"),
            "fatal_ffmpeg_warning",
        )


class FullDecodeValidationTests(unittest.TestCase):
    @patch("anicat.hls_downloader.subprocess.run")
    def test_zero_exit_with_decode_error_is_rejected(self, run):
        from anicat.hls_downloader import _validate_output

        def fail(command, **kwargs):
            if command[-1] == "-version":
                return subprocess.CompletedProcess(command, 0, stdout=b"ffmpeg version 4.4\n")
            kwargs["stderr"].write(b"error while decoding MB 32 47")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = fail
        with self.assertRaisesRegex(DownloadError, "error while decoding"):
            _validate_output("ffmpeg", Path("video.mp4"), 2)
