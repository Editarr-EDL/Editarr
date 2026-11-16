"""Bounded codec-aware stream sampling shared by v1 and candidate experiments."""

from __future__ import annotations

import hashlib
import math
import struct
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import av

from scanner.pipeline.audio_tracks import _file_state, inventory_from_streams
from scanner.pipeline.bounded_media import BoundedInput, MediaBudgetError
from scanner.pipeline.frames import _playable_video_stream_index

StreamUnsupported = MediaBudgetError


@dataclass(frozen=True)
class SamplingCandidate:
    positions: int
    window_seconds: float


def _framed(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def _h264_configuration(extra: bytes) -> tuple[int, bytes]:
    if len(extra) < 7 or len(extra) > 65536 or extra[0] != 1:
        raise StreamUnsupported("h264_requires_avcc")
    width = (extra[4] & 3) + 1
    if width == 3 or not extra[5] & 31:
        raise StreamUnsupported("invalid_avcc")
    position = 6
    configuration = []
    for count in (extra[5] & 31,):
        for _index in range(count):
            if position + 2 > len(extra):
                raise StreamUnsupported("invalid_avcc")
            length = int.from_bytes(extra[position : position + 2], "big")
            position += 2
            if not length or position + length > len(extra):
                raise StreamUnsupported("invalid_avcc")
            configuration.append(extra[position : position + length])
            position += length
    if position >= len(extra):
        raise StreamUnsupported("invalid_avcc")
    count = extra[position]
    if not count:
        raise StreamUnsupported("invalid_avcc")
    position += 1
    for _index in range(count):
        if position + 2 > len(extra):
            raise StreamUnsupported("invalid_avcc")
        length = int.from_bytes(extra[position : position + 2], "big")
        position += 2
        if not length or position + length > len(extra):
            raise StreamUnsupported("invalid_avcc")
        configuration.append(extra[position : position + length])
        position += length
    return width, extra[1:4] + b"".join(_framed(payload) for payload in configuration) + _framed(
        extra[position:]
    )


def _h264_payload(payload: bytes, width: int) -> bytes:
    position = 0
    normalized = bytearray()
    while position < len(payload):
        if position + width > len(payload):
            raise StreamUnsupported("invalid_nal_packet")
        length = int.from_bytes(payload[position : position + width], "big")
        position += width
        if not length or position + length > len(payload):
            raise StreamUnsupported("invalid_nal_packet")
        normalized.extend(_framed(payload[position : position + length]))
        position += length
    return bytes(normalized)


def sample_stream(
    path: Path,
    stream_index: int | None,
    candidate: SamplingCandidate,
    *,
    should_cancel=None,
    audio_selection=None,
):
    """Measure bounded samples; unsupported indexes/codecs never trigger a full scan."""
    if candidate.positions not in {3, 5} or candidate.window_seconds not in {0.25, 0.5}:
        raise StreamUnsupported("unsupported_sampling_candidate")
    before = _file_state(path)
    source = BoundedInput(path, should_cancel)
    cpu_start = time.process_time()
    packets_seen = 0
    payload_bytes = 0
    raw = hashlib.sha256()
    normalized = hashlib.sha256()
    samples = []
    try:
        with av.open(
            source, options={"probesize": "4194304", "analyzeduration": "2000000"}
        ) as container:
            if len(container.streams) > 128:
                raise StreamUnsupported("stream_budget")
            if audio_selection is not None:
                inventory_from_streams(path, before, container.streams).revalidate(audio_selection)
            if stream_index is None:
                stream_index = _playable_video_stream_index(container.streams.video)
            stream = next(
                (stream for stream in container.streams if stream.index == stream_index), None
            )
            if stream is None or stream.type not in {"audio", "video"}:
                raise StreamUnsupported("missing_stream")
            codec = stream.codec_context.name
            extra = stream.codec_context.extradata or b""
            if codec == "h264" and stream.type == "video":
                width, configuration = _h264_configuration(extra)
            elif (
                codec == "aac"
                and stream.type == "audio"
                and 2 <= len(extra) <= 65536
                and extra[0] >> 3 == 2
            ):
                width, configuration = 0, extra
            else:
                raise StreamUnsupported("unsupported_codec_configuration")
            if stream.duration is None or stream.time_base is None or stream.start_time is None:
                raise StreamUnsupported("missing_stream_timing_index")
            start = float(stream.start_time * stream.time_base)
            duration = float(stream.duration * stream.time_base)
            if (
                not math.isfinite(start)
                or not math.isfinite(duration)
                or duration <= candidate.window_seconds
            ):
                raise StreamUnsupported("stream_too_short")
            raw.update(_framed(extra))
            normalized.update(_framed(configuration))
            for sample_index in range(candidate.positions):
                source.check()
                target = start + (duration - candidate.window_seconds) * sample_index / (
                    candidate.positions - 1
                )
                container.seek(int(target / stream.time_base), stream=stream, backward=True)
                collected = []
                for packet in container.demux(stream):
                    source.check()
                    packets_seen += 1
                    if packets_seen > 20000:
                        raise StreamUnsupported("packet_budget")
                    if not packet.size:
                        continue
                    if packet.pts is None or packet.time_base is None:
                        raise StreamUnsupported("missing_packet_timestamp")
                    seconds = float(packet.pts * packet.time_base)
                    if seconds < target:
                        continue
                    if seconds >= target + candidate.window_seconds:
                        break
                    payload_bytes += packet.size
                    if payload_bytes > 3 * 1024 * 1024:
                        raise StreamUnsupported("payload_budget")
                    payload = bytes(packet)
                    raw.update(_framed(payload))
                    converted = _h264_payload(payload, width) if width else payload
                    normalized.update(_framed(converted))
                    collected.append(converted)
                if not collected:
                    raise StreamUnsupported("empty_sample")
                samples.append(collected)
            source.check()
            if _file_state(path) != before:
                raise StreamUnsupported("file_changed")
            return {
                "candidate": asdict(candidate),
                "codec": codec,
                "channel": stream.type,
                "raw_digest": raw.hexdigest(),
                "normalized_digest": normalized.hexdigest(),
                "timing": {
                    "start_seconds": start,
                    "duration_seconds": duration,
                    "time_base": str(stream.time_base),
                    "playback_start": (
                        str(
                            Fraction(stream.start_time * stream.time_base)
                            - Fraction(container.start_time, av.time_base)
                        )
                        if container.start_time is not None
                        else None
                    ),
                    "duration": str(Fraction(stream.duration * stream.time_base)),
                },
                "bytes_read": source.bytes_read,
                "read_calls": source.read_calls,
                "seeks": source.seeks,
                "packets_seen": packets_seen,
                "payload_bytes": payload_bytes,
                "wall_seconds": time.monotonic() - source.started,
                "cpu_seconds": time.process_time() - cpu_start,
                "configuration": configuration,
                "samples": samples,
            }
    finally:
        source.source.close()
