"""Optional real FFmpeg test using a generated local HLS video."""

import functools
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anicat.errors import DownloadError
from anicat.hls_downloader import download_hls_episode
from anicat.models import Episode


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not on PATH")
class HlsRuntimeTests(unittest.TestCase):
    def test_remux_http_hls_into_decodable_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            subprocess.run(
                [
                    executable,
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000",
                    "-c:a",
                    "aac",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-f",
                    "hls",
                    "-hls_time",
                    "1",
                    str(root / "index.m3u8"),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                episode = Episode(origin, "fixture", f"{origin}/index.m3u8", stream_type="hls")
                result = download_hls_episode(episode, root / "output", timeout=5)
                self.assertGreater(result.bytes_written, 0)
                subprocess.run(
                    [
                        executable,
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-i",
                        str(result.path),
                        "-f",
                        "null",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                # A missing source segment must not replace a completed output.
                before = result.path.read_bytes()
                for segment in root.glob("*.ts"):
                    segment.unlink()
                with self.assertRaises(DownloadError):
                    download_hls_episode(episode, root / "output", timeout=5, overwrite=True)
                self.assertEqual(result.path.read_bytes(), before)
                self.assertEqual(list((root / "output").iterdir()), [result.path])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
