"""Optional real FFmpeg test using a generated local HLS video."""

import functools
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from anicat.errors import DownloadError
from anicat.hls_downloader import download_hls_episode
from anicat.hls_segments import download_segments, parse_media_segments
from anicat.models import Episode


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class AesFaultHandler(QuietHandler):
    mode = "normal"

    def _write_body(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/keys/a.key") and self.mode == "wrong-key":
            self._write_body(b"wrong-key-value!")
            return
        if self.path.endswith("/keys/a.key") and self.mode == "short-key":
            self._write_body(b"short-key-value")
            return
        if self.path.endswith("/encrypted-0.ts") and self.mode == "truncated":
            body = (Path(self.directory) / "encrypted-0.ts").read_bytes()[:-16]
            self._write_body(body)
            return
        if self.path.endswith("/encrypted-0.ts") and self.mode == "bad-padding":
            body = bytearray((Path(self.directory) / "encrypted-0.ts").read_bytes())
            body[-1] ^= 1
            self._write_body(bytes(body))
            return
        super().do_GET()


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


def _aes_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _generate_encrypted_hls_fixture(executable: str, root: Path) -> tuple[bytes, bytes, bytes]:
    _generate_hls_fixture(executable, root)
    source_playlist = root / "index.m3u8"
    source_lines = source_playlist.read_text().splitlines()
    source_segments = [line for line in source_lines if line.endswith(".ts")]
    source_durations = [
        line.partition(":")[2].partition(",")[0]
        for line in source_lines
        if line.startswith("#EXTINF:")
    ]
    key_a = b"A" * 16
    key_b = b"B" * 16
    explicit_iv = bytes.fromhex("10" * 16)
    key_directory = root / "keys"
    key_directory.mkdir()
    (key_directory / "a.key").write_bytes(key_a)
    (key_directory / "b.key").write_bytes(key_b)
    encrypted_names: list[str] = []
    for index, source_name in enumerate(source_segments):
        name = f"encrypted-{index}.ts"
        encrypted_names.append(name)
        iv = (7 + index).to_bytes(16, "big") if index < 2 else explicit_iv
        key = key_a if index < 2 else key_b
        encrypted = _aes_encrypt((root / source_name).read_bytes(), key, iv)
        (root / name).write_bytes(encrypted)
    playlist_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:1",
        "#EXT-X-MEDIA-SEQUENCE:7",
        '#EXT-X-KEY:METHOD=AES-128,URI="keys/a.key"',
    ]
    for index, name in enumerate(encrypted_names):
        if index == 2:
            playlist_lines.append(
                '#EXT-X-KEY:METHOD=AES-128,URI="keys/b.key",IV=0x' + explicit_iv.hex()
            )
        duration = source_durations[index]
        playlist_lines.extend((f"#EXTINF:{duration},", name))
    playlist_lines.append("#EXT-X-ENDLIST")
    source_playlist.write_text("\n".join(playlist_lines) + "\n")
    return key_a, key_b, explicit_iv


def _generate_audio_format_change_fixture(executable: str, root: Path) -> None:
    base = [
        executable,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10",
        "-f",
        "lavfi",
    ]
    for index, (frequency, sample_rate, channels) in enumerate(((440, 44100, 1), (660, 48000, 2))):
        subprocess.run(
            [
                *base,
                "-i",
                f"sine=frequency={frequency}:sample_rate={sample_rate}",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "10",
                "-keyint_min",
                "10",
                "-sc_threshold",
                "0",
                "-c:a",
                "aac",
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                "-f",
                "mpegts",
                str(root / f"format{index}.ts"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    (root / "index.m3u8").write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:2\n"
        "#EXTINF:2.000000,\n"
        "format0.ts\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:2.000000,\n"
        "format1.ts\n"
        "#EXT-X-ENDLIST\n"
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
                    payload_start = 5 + packet[4] if adaptation_control in (2, 3) else 4
                    payload = packet[payload_start:]
                    if packet[1] & 0x40 and payload[:3] == b"\0\0\1":
                        payload_start += 9 + payload[8]
                    else:
                        payload_start += 20
                    corrupted[start + payload_start : start + 188] = b"\0" * (188 - payload_start)
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

    def test_audio_format_change_is_remuxed_per_discontinuity_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_audio_format_change_fixture(executable, root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                episode = Episode(
                    origin, "audio-format-change", f"{origin}/index.m3u8", stream_type="hls"
                )
                result = download_hls_episode(episode, root / "output", timeout=5, retries=0)
                self.assertGreater(result.bytes_written, 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not on PATH")
class HlsAesRuntimeTests(unittest.TestCase):
    def test_aes128_rotation_implicit_iv_and_key_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_encrypted_hls_fixture(executable, root)

            class KeyRetryHandler(QuietHandler):
                key_attempts = 0

                def do_GET(self):
                    if self.path.endswith("/keys/a.key"):
                        type(self).key_attempts += 1
                        if type(self).key_attempts == 1:
                            self.send_error(503)
                            return
                    super().do_GET()

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(KeyRetryHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                episode = Episode(origin, "aes-fixture", f"{origin}/index.m3u8", stream_type="hls")
                result = download_hls_episode(episode, root / "output", timeout=5, retries=1)
                self.assertGreater(result.bytes_written, 0)
                self.assertEqual(KeyRetryHandler.key_attempts, 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_aes128_same_uri_rotation_uses_declaration_scoped_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_encrypted_hls_fixture(executable, root)
            playlist = root / "index.m3u8"
            playlist.write_text(
                playlist.read_text().replace('URI="keys/b.key"', 'URI="keys/a.key"')
            )

            class SameUriRotationHandler(QuietHandler):
                key_attempts = 0

                def do_GET(self):
                    if self.path.endswith("/keys/a.key"):
                        type(self).key_attempts += 1
                        body = b"A" * 16 if type(self).key_attempts == 1 else b"B" * 16
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    super().do_GET()

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(SameUriRotationHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                specs = parse_media_segments(playlist.read_text(), f"{origin}/index.m3u8")
                batch = download_segments(
                    specs,
                    root / "same-uri-segments",
                    timeout=5,
                    retries=0,
                    workers=1,
                )
                self.assertEqual(SameUriRotationHandler.key_attempts, 2)
                self.assertEqual(batch.summary["encrypted_segments"], 3)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_aes128_bad_key_padding_and_ciphertext_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = shutil.which("ffmpeg")
            assert executable is not None
            _generate_encrypted_hls_fixture(executable, root)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(AesFaultHandler, directory=directory)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                playlist = f"{origin}/index.m3u8"
                specs = parse_media_segments((root / "index.m3u8").read_text(), playlist)
                for mode in ("wrong-key", "short-key", "truncated", "bad-padding"):
                    AesFaultHandler.mode = mode
                    with self.subTest(mode=mode):
                        with self.assertRaises(DownloadError):
                            download_segments(
                                specs,
                                root / f"segments-{mode}",
                                timeout=5,
                                retries=0,
                            )

                AesFaultHandler.mode = "normal"
                bad_key = specs[2].key
                assert bad_key is not None
                bad_specs = (*specs[:2], replace(specs[2], key=replace(bad_key, iv=b"\0" * 16)))
                with self.assertRaisesRegex(DownloadError, "invalid_sync_byte|invalid_aes_padding"):
                    download_segments(
                        bad_specs,
                        root / "segments-wrong-iv",
                        timeout=5,
                        retries=0,
                    )
            finally:
                AesFaultHandler.mode = "normal"
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
