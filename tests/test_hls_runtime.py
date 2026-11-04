"""Optional real FFmpeg test using a generated local HLS video."""

import functools
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anicat.errors import DownloadError
from anicat.hls_downloader import download_hls_episode
from anicat.models import Episode


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def _generate_hls_fixture(executable: str, root: Path) -> None:
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
            "3",
            "-c:v",
            "libx264",
            "-g",
            "10",
            "-keyint_min",
            "10",
            "-sc_threshold",
            "0",
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


def _reset_segment_counters(path: Path, initial: dict[int, int] | None = None) -> dict[int, int]:
    data = bytearray(path.read_bytes())
    counters = dict(initial or {})
    for offset in range(0, len(data), 188):
        packet = data[offset : offset + 188]
        if packet[0] != 0x47:
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        adaptation_control = (packet[3] >> 4) & 0x03
        if adaptation_control in (1, 3) and pid != 0x1FFF:
            counter = counters.get(pid, 0)
            packet[3] = (packet[3] & 0xF0) | counter
            counters[pid] = (counter + 1) % 16
            data[offset : offset + 188] = packet
    path.write_bytes(data)
    return counters


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not on PATH")
class HlsRuntimeTests(unittest.TestCase):
    def test_remux_http_hls_into_decodable_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_hls_fixture(executable, root)
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
                segments = sorted(root.glob("*.ts"))
                self.assertGreaterEqual(len(segments), 3)
                subprocess.run(
                    [
                        executable,
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-i",
                        str(result.path),
                        "-vsync",
                        "0",
                        "-enc_time_base",
                        "-1",
                        "-f",
                        "null",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                # A missing middle source segment must not replace a completed output.
                before = result.path.read_bytes()
                middle = segments[len(segments) // 2]
                middle_bytes = middle.read_bytes()
                middle.unlink()
                with self.assertRaises(DownloadError):
                    download_hls_episode(
                        episode, root / "output", timeout=5, overwrite=True, retries=1
                    )
                self.assertEqual(result.path.read_bytes(), before)
                self.assertEqual(list((root / "output").iterdir()), [result.path])

                # A truncated middle segment must also fail instead of producing a gap.
                middle.write_bytes(middle_bytes[:64])
                with self.assertRaises(DownloadError):
                    download_hls_episode(
                        episode, root / "output", timeout=5, overwrite=True, retries=1
                    )
                self.assertEqual(result.path.read_bytes(), before)
                self.assertEqual(list((root / "output").iterdir()), [result.path])
                middle.write_bytes(middle_bytes)

                # Removing one complete TS packet leaves a multiple of 188,
                # so the per-PID continuity check must catch it.
                middle.write_bytes(middle_bytes[: 188 * 4] + middle_bytes[188 * 5 :])
                with self.assertRaises(DownloadError):
                    download_hls_episode(
                        episode, root / "output", timeout=5, overwrite=True, retries=0
                    )
                self.assertEqual(result.path.read_bytes(), before)
                middle.write_bytes(middle_bytes)

                # Keep every TS header and CC valid but damage codec payload;
                # the complete FFmpeg decode must still reject the candidate.
                corrupted = bytearray(middle_bytes)
                damaged_video_packets = 0
                for packet_number in range(1, len(corrupted) // 188):
                    start = packet_number * 188
                    packet = corrupted[start : start + 188]
                    pid = ((packet[1] & 0x1F) << 8) | packet[2]
                    if pid != 256:
                        continue
                    adaptation_control = (packet[3] >> 4) & 0x03
                    payload_start = (
                        5 + packet[4] if adaptation_control in (2, 3) else 4
                    )
                    payload = packet[payload_start:]
                    if packet[1] & 0x40 and payload[:3] == b"\0\0\1":
                        payload_start += 9 + payload[8]
                    else:
                        payload_start += 20
                    corrupted[start + payload_start : start + 188] = b"\0" * (
                        188 - payload_start
                    )
                    damaged_video_packets += 1
                    if damaged_video_packets == 10:
                        break
                middle.write_bytes(corrupted)
                with self.assertRaises(DownloadError):
                    download_hls_episode(
                        episode, root / "output", timeout=5, overwrite=True, retries=0
                    )
                self.assertEqual(result.path.read_bytes(), before)
                middle.write_bytes(middle_bytes)
                for fault in ("503", "timeout"):

                    class TransientHandler(QuietHandler):
                        mode = fault
                        attempts = 0

                        def do_GET(self):
                            if self.path.endswith(middle.name):
                                type(self).attempts += 1
                                if type(self).attempts == 1:
                                    if self.mode == "503":
                                        self.send_error(503)
                                    else:
                                        time.sleep(0.4)
                                        self.close_connection = True
                                    return
                            super().do_GET()

                    server.RequestHandlerClass = functools.partial(
                        TransientHandler, directory=directory
                    )
                    with self.subTest(fault=fault):
                        recovered = download_hls_episode(
                            episode,
                            root / "output",
                            timeout=0.1,
                            overwrite=True,
                            retries=1,
                        )
                        self.assertGreaterEqual(TransientHandler.attempts, 2)
                        self.assertGreater(recovered.bytes_written, 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_explicit_discontinuity_with_segment_counter_reset_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_hls_fixture(executable, root)
            playlist = root / "index.m3u8"
            lines = playlist.read_text().splitlines()
            segment_names = [line for line in lines if line.endswith(".ts")]
            second_segment = segment_names[1]
            next_counters = _reset_segment_counters(root / second_segment)
            _reset_segment_counters(root / segment_names[2], next_counters)
            lines.insert(lines.index(second_segment), "#EXT-X-DISCONTINUITY")
            playlist.write_text("\n".join(lines) + "\n")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                episode = Episode(
                    origin, "counter-reset", f"{origin}/index.m3u8", stream_type="hls"
                )
                result = download_hls_episode(episode, root / "output", timeout=5)
                self.assertGreater(result.bytes_written, 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
