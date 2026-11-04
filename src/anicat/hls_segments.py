"""Download and validate MPEG-TS HLS segments before handing them to FFmpeg.

The provider occasionally resets MPEG-TS continuity counters at a media
segment boundary. A reset at an explicitly declared discontinuity boundary is
recoverable; an unmarked reset or a gap inside one segment is not. This module
keeps those cases separate and never lets a missing or unvalidated segment
reach the remuxer.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .errors import DownloadError

TS_PACKET_SIZE = 188
_TS_SYNC_BYTE = 0x47
_PTS_MODULUS = 1 << 33
_PTS_HALF_RANGE = 1 << 32
_DEFAULT_WORKERS = 4
_CHUNK_SIZE = 256 * 1024


class SegmentIntegrityError(DownloadError):
    """A segment is present but its MPEG-TS structure is not trustworthy."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class SegmentSpec:
    """One media segment from a final HLS media playlist."""

    uri: str
    duration: float
    discontinuity_before: bool = False


@dataclass(frozen=True)
class SegmentInspection:
    """Evidence collected from one complete MPEG-TS segment."""

    segment_index: int
    packet_count: int
    size: int
    sha256: str
    first_cc: tuple[tuple[int, int], ...]
    last_cc: tuple[tuple[int, int], ...]
    first_pts: tuple[tuple[int, int], ...]
    last_pts: tuple[tuple[int, int], ...]

    @property
    def first_cc_by_pid(self) -> dict[int, int]:
        return dict(self.first_cc)

    @property
    def last_cc_by_pid(self) -> dict[int, int]:
        return dict(self.last_cc)

    @property
    def first_pts_by_pid(self) -> dict[int, int]:
        return dict(self.first_pts)

    @property
    def last_pts_by_pid(self) -> dict[int, int]:
        return dict(self.last_pts)


@dataclass(frozen=True)
class BoundaryEvidence:
    """Why a local HLS discontinuity marker was inserted before a segment."""

    index: int
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return ",".join(self.reasons)


@dataclass(frozen=True)
class DownloadedSegment:
    """A segment path together with the structural evidence for it."""

    path: Path
    inspection: SegmentInspection
    attempts: int = 1


@dataclass(frozen=True)
class SegmentBatch:
    """All downloaded segments and boundary evidence in playlist order."""

    segments: tuple[DownloadedSegment, ...]
    boundaries: tuple[BoundaryEvidence, ...]

    @property
    def summary(self) -> dict[str, object]:
        """Return compact, structured evidence for logs and acceptance tests."""

        return {
            "segments": [
                {
                    "index": item.inspection.segment_index,
                    "bytes": item.inspection.size,
                    "sha256": item.inspection.sha256,
                    "attempts": item.attempts,
                }
                for item in self.segments
            ],
            "boundaries": [
                {"index": item.index, "classification": item.label} for item in self.boundaries
            ],
        }


@dataclass(frozen=True)
class ContinuityNormalization:
    """Evidence for CC rewrites applied only at explicit boundaries."""

    segment_index: int
    offsets: tuple[tuple[int, int], ...]
    packets_rewritten: int


_session_state = threading.local()


def _thread_session() -> requests.Session:
    """Reuse one requests session per worker thread, never across workers."""

    session = getattr(_session_state, "session", None)
    if session is None:
        session = requests.Session()
        _session_state.session = session
    return session


def _parse_attribute(attributes: str, wanted: str) -> str | None:
    """Read one HLS attribute without splitting commas inside quoted values."""

    quoted = False
    current: list[str] = []
    values: list[str] = []
    for character in attributes:
        if character == '"':
            quoted = not quoted
        if character == "," and not quoted:
            values.append("".join(current))
            current = []
        else:
            current.append(character)
    values.append("".join(current))
    for value in values:
        key, separator, raw = value.partition("=")
        if separator and key.strip().upper() == wanted.upper():
            return raw.strip().strip('"')
    return None


def parse_media_segments(playlist: str, playlist_url: str) -> tuple[SegmentSpec, ...]:
    """Parse a final MPEG-TS media playlist and reject unsupported HLS modes."""

    segments: list[SegmentSpec] = []
    pending_duration: float | None = None
    pending_discontinuity = False
    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            raise DownloadError("unsupported HLS fMP4 playlist (#EXT-X-MAP)")
        if line.startswith("#EXT-X-BYTERANGE:"):
            raise DownloadError("unsupported HLS byte-range segment")
        if line.startswith("#EXT-X-GAP"):
            raise DownloadError("unsupported HLS gap segment")
        if line.startswith("#EXT-X-KEY:"):
            method = _parse_attribute(line.partition(":")[2], "METHOD")
            if method is None or method.upper() != "NONE":
                raise DownloadError(f"unsupported HLS encryption method: {method or 'unknown'}")
        if line.startswith(("#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:")):
            raise DownloadError("unsupported HLS low-latency playlist")
        if line.startswith("#EXT-X-I-FRAMES-ONLY"):
            raise DownloadError("unsupported HLS I-frames-only playlist")
        if line.startswith("#EXT-X-DISCONTINUITY"):
            pending_discontinuity = True
            continue
        if line.startswith("#EXTINF:"):
            if pending_duration is not None:
                raise DownloadError("HLS playlist has consecutive EXTINF tags")
            raw_duration = line.partition(":")[2].partition(",")[0].strip()
            try:
                pending_duration = float(raw_duration)
            except ValueError as error:
                raise DownloadError(f"invalid HLS segment duration: {raw_duration}") from error
            if not math.isfinite(pending_duration) or pending_duration <= 0:
                raise DownloadError(f"invalid HLS segment duration: {raw_duration}")
            continue
        if line.startswith("#"):
            continue
        if pending_duration is None:
            raise DownloadError(f"HLS segment URI has no EXTINF duration: {line}")
        absolute_uri = urljoin(playlist_url, line)
        suffix = urlparse(absolute_uri).path.lower()
        if suffix.endswith((".m4s", ".mp4", ".cmfv", ".cmfa")):
            raise DownloadError("unsupported HLS fMP4 media segment")
        segments.append(
            SegmentSpec(
                absolute_uri,
                pending_duration,
                pending_discontinuity,
            )
        )
        pending_duration = None
        pending_discontinuity = False

    if pending_duration is not None:
        raise DownloadError("HLS playlist ends with an EXTINF without a segment URI")
    if not segments:
        raise DownloadError(f"HLS playlist contains no media segments: {playlist_url}")
    return tuple(segments)


def _payload_start(packet: bytes) -> int | None:
    """Return the payload offset for one TS packet, if it has payload."""

    adaptation_control = (packet[3] >> 4) & 0x03
    if adaptation_control == 0:
        raise SegmentIntegrityError("invalid_adaptation_control", "reserved value 0")
    if adaptation_control in (2, 3):
        adaptation_length = packet[4]
        payload_start = 5 + adaptation_length
        if payload_start > TS_PACKET_SIZE:
            raise SegmentIntegrityError(
                "invalid_adaptation_length",
                f"adaptation field reaches byte {payload_start}",
            )
    else:
        payload_start = 4
    return (
        payload_start if adaptation_control in (1, 3) and payload_start < TS_PACKET_SIZE else None
    )


def _has_discontinuity_indicator(packet: bytes) -> bool:
    adaptation_control = (packet[3] >> 4) & 0x03
    if adaptation_control not in (2, 3) or packet[4] == 0:
        return False
    return bool(packet[5] & 0x80)


def _decode_pts(value: bytes) -> int | None:
    """Decode the five-byte MPEG PES PTS field when marker bits are valid."""

    if len(value) < 5 or (value[0] & 1) != 1 or (value[2] & 1) != 1 or (value[4] & 1) != 1:
        return None
    return (
        ((value[0] >> 1) & 0x07) << 30
        | (value[1] << 22)
        | ((value[2] >> 1) & 0x7F) << 15
        | (value[3] << 7)
        | ((value[4] >> 1) & 0x7F)
    )


def _pes_pts(payload: bytes) -> int | None:
    """Extract DTS, or PTS when DTS is absent, from a PES payload start."""

    if len(payload) < 14 or payload[:3] != b"\x00\x00\x01":
        return None
    flags = (payload[7] >> 6) & 0x03
    if flags not in (2, 3):
        return None
    header_length = payload[8]
    if header_length < 5 or len(payload) < 9 + 5:
        return None
    if flags == 3 and header_length >= 10 and len(payload) >= 19:
        dts = _decode_pts(payload[14:19])
        if dts is not None:
            return dts
    return _decode_pts(payload[9:14])


def _pts_delta(previous: int, current: int) -> int:
    delta = (current - previous) % _PTS_MODULUS
    return delta - _PTS_MODULUS if delta >= _PTS_HALF_RANGE else delta


def inspect_ts_segment(data: bytes, segment_index: int = 0) -> SegmentInspection:
    """Validate TS framing and per-PID continuity within one segment."""

    if not data:
        raise SegmentIntegrityError("empty_segment", f"segment {segment_index} is empty")
    if len(data) % TS_PACKET_SIZE:
        raise SegmentIntegrityError(
            "truncated_segment",
            f"segment {segment_index} has {len(data)} bytes, not a multiple of 188",
        )
    packet_count = len(data) // TS_PACKET_SIZE
    previous_cc: dict[int, int] = {}
    previous_payload: dict[int, bytes] = {}
    pending_discontinuity: set[int] = set()
    first_cc: dict[int, int] = {}
    last_cc: dict[int, int] = {}
    first_pts: dict[int, int] = {}
    last_pts: dict[int, int] = {}
    video_pids: set[int] = set()
    for packet_number in range(packet_count):
        offset = packet_number * TS_PACKET_SIZE
        packet = data[offset : offset + TS_PACKET_SIZE]
        if packet[0] != _TS_SYNC_BYTE:
            raise SegmentIntegrityError(
                "invalid_sync_byte",
                f"segment {segment_index} packet {packet_number} does not start with 0x47",
            )
        if packet[1] & 0x80:
            raise SegmentIntegrityError(
                "transport_error",
                f"segment {segment_index} packet {packet_number} has TEI set",
            )
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        payload_start = _payload_start(packet)
        if _has_discontinuity_indicator(packet):
            pending_discontinuity.add(pid)
        if payload_start is not None:
            continuity_counter = packet[3] & 0x0F
            previous = previous_cc.get(pid)
            payload = packet[payload_start:]
            if pid != 0x1FFF and previous is not None:
                expected = (previous + 1) % 16
                duplicate = continuity_counter == previous and payload == previous_payload.get(pid)
                has_discontinuity = pid in pending_discontinuity
                if continuity_counter != expected and not duplicate and not has_discontinuity:
                    raise SegmentIntegrityError(
                        "continuity_gap",
                        f"segment {segment_index} PID {pid} expected {expected}, "
                        f"got {continuity_counter} at packet {packet_number}",
                    )
            if pid != 0x1FFF:
                first_cc.setdefault(pid, continuity_counter)
                previous_cc[pid] = continuity_counter
                last_cc[pid] = continuity_counter
                previous_payload[pid] = payload
                pending_discontinuity.discard(pid)
            if packet[1] & 0x40:
                pts = _pes_pts(packet[payload_start:])
                if pts is not None:
                    first_pts.setdefault(pid, pts)
                    last_pts[pid] = pts
                pes_payload = packet[payload_start:]
                if len(pes_payload) >= 9 and pes_payload[:3] == b"\x00\x00\x01":
                    stream_id = pes_payload[3]
                    header_end = 9 + pes_payload[8]
                    if 0xE0 <= stream_id <= 0xEF and header_end <= len(pes_payload):
                        video_pids.add(pid)
                        pes_payload = pes_payload[header_end:]
                    else:
                        pes_payload = b""
                else:
                    pes_payload = b""
            elif pid in video_pids:
                pes_payload = payload
            else:
                pes_payload = b""
            # shortcut: H.264 emulation prevention forbids long zero runs in a NAL;
            # FFmpeg's complete decode remains the final check for other payload damage.
            if pes_payload and b"\x00" * 32 in pes_payload:
                raise SegmentIntegrityError(
                    "video_payload_corruption",
                    f"segment {segment_index} PID {pid} contains an invalid zero run",
                )
    return SegmentInspection(
        segment_index,
        packet_count,
        len(data),
        hashlib.sha256(data).hexdigest(),
        tuple(sorted(first_cc.items())),
        tuple(sorted(last_cc.items())),
        tuple(sorted(first_pts.items())),
        tuple(sorted(last_pts.items())),
    )


def classify_boundaries(
    specs: tuple[SegmentSpec, ...], inspections: tuple[SegmentInspection, ...]
) -> tuple[BoundaryEvidence, ...]:
    """Classify only segment-boundary counter/PTS resets as recoverable."""

    if len(specs) != len(inspections):
        raise ValueError("segment specification and inspection counts differ")
    boundaries: list[BoundaryEvidence] = []
    if specs and specs[0].discontinuity_before:
        boundaries.append(BoundaryEvidence(0, ("manifest_discontinuity",)))
    for index in range(1, len(inspections)):
        reasons: list[str] = []
        previous = inspections[index - 1]
        current = inspections[index]
        previous_cc = previous.last_cc_by_pid
        current_cc = current.first_cc_by_pid
        for pid in sorted(previous_cc.keys() & current_cc.keys()):
            expected = (previous_cc[pid] + 1) % 16
            if current_cc[pid] != expected:
                reasons.append(f"pid_{pid}_cc_reset")
        previous_pts = previous.last_pts_by_pid
        current_pts = current.first_pts_by_pid
        for pid in sorted(previous_pts.keys() & current_pts.keys()):
            if _pts_delta(previous_pts[pid], current_pts[pid]) < 0:
                reasons.append(f"pid_{pid}_pts_reset")
        if not reasons:
            if specs[index].discontinuity_before:
                boundaries.append(BoundaryEvidence(index, ("manifest_discontinuity",)))
            continue
        if not specs[index].discontinuity_before:
            raise SegmentIntegrityError(
                "unmarked_boundary_discontinuity",
                f"segment boundary {index} has {', '.join(reasons)} without #EXT-X-DISCONTINUITY",
            )
        boundaries.append(
            BoundaryEvidence(
                index,
                tuple(dict.fromkeys(("manifest_discontinuity", *reasons))),
            )
        )
    return tuple(boundaries)


def _retryable_http_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def _download_one(
    spec: SegmentSpec,
    index: int,
    directory: Path,
    *,
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
    timeout: float,
    retries: int,
) -> DownloadedSegment:
    """Download one segment from scratch on each bounded retry."""

    path = directory / f"segment-{index:06d}.ts"
    partial = directory / f"segment-{index:06d}.part"
    last_error: Exception | None = None
    attempts = 0
    for attempt in range(retries + 1):
        attempts = attempt + 1
        partial.unlink(missing_ok=True)
        response: requests.Response | None = None
        retryable = False
        try:
            response = _thread_session().get(
                spec.uri,
                headers={**headers, "Accept-Encoding": "identity"},
                cookies=dict(cookies),
                stream=True,
                timeout=(timeout, timeout),
            )
            if response.status_code != 200:
                retryable = _retryable_http_status(response.status_code)
                raise DownloadError(f"HTTP {response.status_code} for segment {index}")
            if response.headers.get("Content-Range"):
                raise DownloadError(f"unexpected Content-Range for segment {index}")
            content_encoding = response.headers.get("Content-Encoding", "identity").lower()
            if content_encoding not in {"", "identity"}:
                raise DownloadError(f"unexpected Content-Encoding for segment {index}")
            raw_length = response.headers.get("Content-Length")
            expected_length: int | None = None
            if raw_length is not None:
                try:
                    expected_length = int(raw_length)
                except ValueError as error:
                    raise DownloadError(f"invalid Content-Length for segment {index}") from error
                if expected_length < 1:
                    raise DownloadError(f"empty Content-Length for segment {index}")
            written = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        output.write(chunk)
                        written += len(chunk)
            if written == 0:
                raise DownloadError(f"empty segment {index}")
            if expected_length is not None and written != expected_length:
                retryable = True
                raise DownloadError(
                    f"truncated segment {index}: expected {expected_length} bytes, got {written}"
                )
            data = partial.read_bytes()
            inspection = inspect_ts_segment(data, index)
            partial.replace(path)
            return DownloadedSegment(path, inspection, attempts)
        except (requests.RequestException, OSError, DownloadError) as error:
            last_error = error
            if isinstance(error, requests.RequestException):
                retryable = True
            if attempt >= retries or not retryable:
                break
        finally:
            if response is not None:
                response.close()
        # A retry always starts from byte zero; no unverified partial is reused.
    partial.unlink(missing_ok=True)
    detail = str(last_error) if last_error is not None else "unknown segment failure"
    raise DownloadError(f"HLS segment {index} failed after {attempts} attempt(s): {detail}")


def download_segments(
    specs: tuple[SegmentSpec, ...],
    directory: Path,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    timeout: float,
    retries: int,
    workers: int = _DEFAULT_WORKERS,
) -> SegmentBatch:
    """Download and validate every segment before producing a local playlist."""

    if not specs:
        raise DownloadError("HLS playlist contains no media segments")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    if retries < 0:
        raise ValueError("retries must be greater than or equal to 0")
    if workers < 1:
        raise ValueError("workers must be greater than 0")
    directory.mkdir(parents=True, exist_ok=True)
    request_headers = dict(headers or {})
    request_cookies = dict(cookies or {})
    downloaded: list[DownloadedSegment | None] = [None] * len(specs)
    worker_count = min(workers, len(specs))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="anicat-hls") as pool:
        futures = {
            pool.submit(
                _download_one,
                spec,
                index,
                directory,
                headers=request_headers,
                cookies=request_cookies,
                timeout=timeout,
                retries=retries,
            ): index
            for index, spec in enumerate(specs)
        }
        try:
            for future in as_completed(futures):
                index = futures[future]
                downloaded[index] = future.result()
        except Exception:
            for future in futures:
                future.cancel()
            raise
    completed = tuple(item for item in downloaded if item is not None)
    if len(completed) != len(specs):
        raise DownloadError("HLS segment batch completed with missing results")
    inspections = tuple(item.inspection for item in completed)
    boundaries = classify_boundaries(specs, inspections)
    return SegmentBatch(completed, boundaries)


def write_local_playlist(
    path: Path,
    specs: tuple[SegmentSpec, ...],
    batch: SegmentBatch,
) -> tuple[ContinuityNormalization, ...]:
    """Write a local, fully materialized playlist with evidence-based markers."""

    if len(specs) != len(batch.segments):
        raise ValueError("segment specification and download counts differ")
    markers = {boundary.index: boundary for boundary in batch.boundaries}
    target_duration = max(1, math.ceil(max(spec.duration for spec in specs)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    normalized_directory = path.parent / "normalized"
    normalized_directory.mkdir(parents=True, exist_ok=True)
    previous_last_cc: dict[int, int] = {}
    running_offsets: dict[int, int] = {}
    normalizations: list[ContinuityNormalization] = []
    for index, (spec, downloaded) in enumerate(zip(specs, batch.segments, strict=True)):
        boundary = markers.get(index)
        if boundary is not None:
            lines.append("#EXT-X-DISCONTINUITY")
            lines.append(f"# ANICAT-EVIDENCE:{boundary.label}")
        original = downloaded.path.read_bytes()
        offsets = dict(running_offsets)
        first_cc = downloaded.inspection.first_cc_by_pid
        if index and boundary is not None:
            for pid, current in first_cc.items():
                previous = previous_last_cc.get(pid)
                if previous is not None:
                    offsets[pid] = ((previous + 1) - current) % 16
        normalized = bytearray(original)
        rewritten = 0
        if offsets:
            for packet_offset in range(0, len(normalized), TS_PACKET_SIZE):
                packet = normalized[packet_offset : packet_offset + TS_PACKET_SIZE]
                pid = ((packet[1] & 0x1F) << 8) | packet[2]
                adaptation_control = (packet[3] >> 4) & 0x03
                if pid in offsets and adaptation_control in (1, 2, 3):
                    old_counter = packet[3] & 0x0F
                    new_counter = (old_counter + offsets[pid]) % 16
                    if new_counter != old_counter:
                        packet[3] = (packet[3] & 0xF0) | new_counter
                        normalized[packet_offset : packet_offset + TS_PACKET_SIZE] = packet
                        rewritten += 1
        normalized_path = normalized_directory / downloaded.path.name
        normalized_path.write_bytes(normalized)
        normalized_inspection = inspect_ts_segment(bytes(normalized), index)
        previous_last_cc = normalized_inspection.last_cc_by_pid
        running_offsets = offsets
        if offsets:
            normalizations.append(
                ContinuityNormalization(index, tuple(sorted(offsets.items())), rewritten)
            )
        lines.append(f"#EXTINF:{spec.duration:.6f},")
        lines.append(normalized_path.relative_to(path.parent).as_posix())
    lines.append("#EXT-X-ENDLIST")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tuple(normalizations)


def write_group_playlists(
    path: Path,
    specs: tuple[SegmentSpec, ...],
) -> tuple[tuple[Path, float], ...]:
    """Split a normalized local playlist at explicit discontinuity boundaries."""

    starts = [0] + [
        index for index, spec in enumerate(specs) if index and spec.discontinuity_before
    ]
    groups: list[tuple[Path, float]] = []
    group_directory = path.parent / "groups"
    group_directory.mkdir(parents=True, exist_ok=True)
    for group_index, start in enumerate(starts):
        end = starts[group_index + 1] if group_index + 1 < len(starts) else len(specs)
        group_specs = specs[start:end]
        target_duration = max(1, math.ceil(max(spec.duration for spec in group_specs)))
        group_path = group_directory / f"group-{group_index:03d}.m3u8"
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_duration}",
            "#EXT-X-MEDIA-SEQUENCE:0",
        ]
        for index in range(start, end):
            normalized = path.parent / "normalized" / f"segment-{index:06d}.ts"
            if not normalized.is_file():
                raise DownloadError(f"normalized HLS segment is missing: {normalized}")
            lines.append(f"#EXTINF:{specs[index].duration:.6f},")
            lines.append(Path(os.path.relpath(normalized, group_directory)).as_posix())
        lines.append("#EXT-X-ENDLIST")
        group_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        groups.append((group_path, sum(spec.duration for spec in group_specs)))
    return tuple(groups)
