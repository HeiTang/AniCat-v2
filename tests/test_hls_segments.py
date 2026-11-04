import hashlib
import tempfile
import unittest
from pathlib import Path

from anicat.errors import DownloadError
from anicat.hls_segments import (
    DownloadedSegment,
    SegmentBatch,
    SegmentIntegrityError,
    SegmentSpec,
    classify_boundaries,
    inspect_ts_segment,
    parse_media_segments,
    write_group_playlists,
    write_local_playlist,
)


def _packet(pid: int, continuity_counter: int, payload: bytes, *, pusi: bool = False) -> bytes:
    header = bytes(
        (
            0x47,
            ((0x40 if pusi else 0) | ((pid >> 8) & 0x1F)),
            pid & 0xFF,
            0x10 | continuity_counter,
        )
    )
    return (header + payload).ljust(188, b"\xff")[:188]


class SegmentInspectionTests(unittest.TestCase):
    def test_valid_segment_records_size_and_sha256(self):
        data = _packet(256, 0, b"first") + _packet(256, 1, b"second")
        evidence = inspect_ts_segment(data, 7)
        self.assertEqual(evidence.segment_index, 7)
        self.assertEqual(evidence.packet_count, 2)
        self.assertEqual(evidence.size, len(data))
        self.assertEqual(evidence.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(evidence.first_cc_by_pid, {256: 0})
        self.assertEqual(evidence.last_cc_by_pid, {256: 1})

    def test_internal_continuity_gap_is_rejected(self):
        data = _packet(256, 0, b"first") + _packet(256, 2, b"missing")
        with self.assertRaisesRegex(SegmentIntegrityError, "continuity_gap"):
            inspect_ts_segment(data, 3)

    def test_duplicate_payload_with_same_counter_is_allowed(self):
        packet = _packet(256, 4, b"repeat")
        evidence = inspect_ts_segment(packet + packet, 4)
        self.assertEqual(evidence.last_cc_by_pid, {256: 4})

    def test_null_packet_counter_is_not_a_stream_continuity_signal(self):
        data = _packet(0x1FFF, 0, b"null") + _packet(0x1FFF, 7, b"null")
        evidence = inspect_ts_segment(data, 5)
        self.assertEqual(evidence.first_cc_by_pid, {})

    def test_adaptation_discontinuity_indicator_allows_internal_reset(self):
        first = _packet(256, 0, b"first")
        second = bytearray(_packet(256, 7, b"reset"))
        second[3] = 0x37
        second[4] = 1
        second[5] = 0x80
        second[6:] = b"reset" + b"\xff" * (188 - 6 - len(b"reset"))
        evidence = inspect_ts_segment(first + bytes(second), 6)
        self.assertEqual(evidence.last_cc_by_pid, {256: 7})

    def test_adaptation_only_discontinuity_allows_next_payload_reset(self):
        first = _packet(256, 0, b"first")
        marker = bytearray(188)
        marker[0:4] = bytes((0x47, 0x01, 0x00, 0x27))
        marker[4] = 1
        marker[5] = 0x80
        marker[6:] = b"\xff" * (188 - 6)
        reset = _packet(256, 7, b"reset")
        evidence = inspect_ts_segment(first + bytes(marker) + reset, 7)
        self.assertEqual(evidence.last_cc_by_pid, {256: 7})


class BoundaryClassificationTests(unittest.TestCase):
    def setUp(self):
        first = inspect_ts_segment(_packet(256, 0, b"first") + _packet(256, 1, b"last"), 0)
        reset = inspect_ts_segment(_packet(256, 0, b"reset") + _packet(256, 1, b"next"), 1)
        self.inspections = (first, reset)

    def test_unmarked_boundary_reset_is_rejected(self):
        specs = (
            SegmentSpec("https://cdn.test/0.ts", 1),
            SegmentSpec("https://cdn.test/1.ts", 1),
        )
        with self.assertRaisesRegex(SegmentIntegrityError, "unmarked_boundary_discontinuity"):
            classify_boundaries(specs, self.inspections)

    def test_explicit_boundary_reset_is_recoverable(self):
        specs = (
            SegmentSpec("https://cdn.test/0.ts", 1),
            SegmentSpec("https://cdn.test/1.ts", 1, discontinuity_before=True),
        )
        boundaries = classify_boundaries(specs, self.inspections)
        self.assertEqual(boundaries[0].index, 1)
        self.assertIn("manifest_discontinuity", boundaries[0].reasons)
        self.assertIn("pid_256_cc_reset", boundaries[0].reasons)


class PlaylistParsingTests(unittest.TestCase):
    def test_unsupported_hls_modes_are_rejected(self):
        fixtures = (
            '#EXT-X-MAP:URI="init.mp4"',
            "#EXT-X-BYTERANGE:188@0",
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"',
            '#EXT-X-KEY:METHOD=AES-128,URI="key",KEYFORMAT="com.example"',
            "#EXT-X-GAP",
        )
        for tag in fixtures:
            with self.subTest(tag=tag):
                playlist = f"#EXTM3U\n#EXTINF:1,\n{tag}\nsegment.ts\n#EXT-X-ENDLIST\n"
                with self.assertRaises(DownloadError):
                    parse_media_segments(playlist, "https://cdn.test/index.m3u8")

    def test_aes_key_rotation_explicit_and_implicit_ivs(self):
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-MEDIA-SEQUENCE:7\n"
            '#EXT-X-KEY:METHOD=AES-128,URI="keys/a.key"\n'
            "#EXTINF:1,\n"
            "a.ts\n"
            "#EXTINF:1,\n"
            "b.ts\n"
            '#EXT-X-KEY:METHOD=AES-128,URI="../keys/b.key",IV=0x2a,'
            'KEYFORMAT="identity",KEYFORMATVERSIONS="1/2"\n'
            "#EXTINF:1,\n"
            "c.ts\n"
            "#EXT-X-KEY:METHOD=NONE\n"
            "#EXTINF:1,\n"
            "d.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        specs = parse_media_segments(playlist, "https://cdn.test/path/index.m3u8")
        self.assertEqual([spec.sequence for spec in specs], [7, 8, 9, 10])
        self.assertIsNotNone(specs[0].key)
        assert specs[0].key is not None
        self.assertEqual(specs[0].key.uri, "https://cdn.test/path/keys/a.key")
        self.assertEqual(specs[0].key.iv, (7).to_bytes(16, "big"))
        second_key = specs[1].key
        assert second_key is not None
        self.assertEqual(specs[0].key.declaration, second_key.declaration)
        self.assertEqual(second_key.iv, (8).to_bytes(16, "big"))
        self.assertIsNotNone(specs[2].key)
        assert specs[2].key is not None
        self.assertEqual(specs[2].key.uri, "https://cdn.test/keys/b.key")
        self.assertEqual(specs[2].key.iv, bytes.fromhex("00" * 15 + "2a"))
        self.assertNotEqual(second_key.declaration, specs[2].key.declaration)
        self.assertIsNone(specs[3].key)

    def test_aes_key_invalid_attributes_are_rejected(self):
        fixtures = (
            '#EXT-X-KEY:METHOD=AES-128,URI="key",IV=0x',
            '#EXT-X-KEY:METHOD=AES-128,URI="key",IV=0x' + "0" * 33,
            '#EXT-X-KEY:METHOD=AES-128,URI="key",IV=0x' + "0" * 30 + " 01",
            '#EXT-X-KEY:METHOD=AES-128,URI="key",KEYFORMATVERSIONS="2"',
            '#EXT-X-KEY:METHOD=AES-128,URI="file:///tmp/key"',
            '#EXT-X-KEY:METHOD=NONE,URI="key"',
        )
        for tag in fixtures:
            with self.subTest(tag=tag):
                playlist = f"#EXTM3U\n{tag}\n#EXTINF:1,\nsegment.ts\n"
                with self.assertRaises(DownloadError):
                    parse_media_segments(playlist, "https://cdn.test/index.m3u8")

    def test_consecutive_extinf_is_rejected(self):
        playlist = "#EXTM3U\n#EXTINF:1,\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
        with self.assertRaisesRegex(DownloadError, "consecutive EXTINF"):
            parse_media_segments(playlist, "https://cdn.test/index.m3u8")

    def test_local_playlist_uses_segment_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment_dir = root / "segments"
            segment_dir.mkdir()
            segment = segment_dir / "segment-000000.ts"
            segment.write_bytes(_packet(256, 0, b"ts"))
            specs = (SegmentSpec("https://cdn.test/0.ts", 1),)
            evidence = inspect_ts_segment(segment.read_bytes(), 0)
            batch = SegmentBatch((DownloadedSegment(segment, evidence),), ())
            playlist = root / "index.m3u8"
            write_local_playlist(playlist, specs, batch)
            self.assertIn("normalized/segment-000000.ts", playlist.read_text())
            groups = write_group_playlists(playlist, specs)
            self.assertEqual(len(groups), 1)
            self.assertIn("../normalized/segment-000000.ts", groups[0][0].read_text())

    def test_explicit_boundary_normalizes_only_continuity_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment_dir = root / "segments"
            segment_dir.mkdir()
            first_path = segment_dir / "segment-000000.ts"
            second_path = segment_dir / "segment-000001.ts"
            third_path = segment_dir / "segment-000002.ts"
            first_path.write_bytes(_packet(256, 0, b"first") + _packet(256, 1, b"last"))
            second_path.write_bytes(_packet(256, 0, b"reset") + _packet(256, 1, b"next"))
            third_path.write_bytes(_packet(256, 2, b"continued") + _packet(256, 3, b"after"))
            specs = (
                SegmentSpec("https://cdn.test/0.ts", 1),
                SegmentSpec("https://cdn.test/1.ts", 1, discontinuity_before=True),
                SegmentSpec("https://cdn.test/2.ts", 1),
            )
            inspections = tuple(
                inspect_ts_segment(path.read_bytes(), index)
                for index, path in enumerate((first_path, second_path, third_path))
            )
            batch = SegmentBatch(
                (
                    DownloadedSegment(first_path, inspections[0]),
                    DownloadedSegment(second_path, inspections[1]),
                    DownloadedSegment(third_path, inspections[2]),
                ),
                classify_boundaries(specs, inspections),
            )
            playlist = root / "index.m3u8"
            normalizations = write_local_playlist(playlist, specs, batch)
            groups = write_group_playlists(playlist, specs)
            self.assertEqual(normalizations[0].segment_index, 1)
            self.assertEqual(len(groups), 2)
            original = second_path.read_bytes()
            normalized = (root / "normalized" / second_path.name).read_bytes()
            for offset in range(0, len(original), 188):
                self.assertEqual(original[offset] & 0xFF, normalized[offset] & 0xFF)
                self.assertEqual(
                    original[offset + 1 : offset + 3], normalized[offset + 1 : offset + 3]
                )
                self.assertEqual(
                    original[offset + 4 : offset + 188], normalized[offset + 4 : offset + 188]
                )
            normalized_third = (root / "normalized" / third_path.name).read_bytes()
            self.assertEqual(inspect_ts_segment(normalized_third, 2).first_cc_by_pid, {256: 4})
