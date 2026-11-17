"""Approved v1 compressed-stream sample framing, independent of local selectors."""

import hashlib
import struct
from collections.abc import Sequence
from typing import Literal

AUDIO_ALGORITHM = "editarr-community-audio-sha256-sampled-1"
PCM_AUDIO_ALGORITHM = "editarr-community-audio-pcm-s16le-16000-mono-sha256-1"
VIDEO_ALGORITHM = "editarr-community-video-sha256-sampled-1"
MAX_PAYLOAD = 3 * 1024 * 1024


def stream_digest(
    channel: Literal["audio", "video"],
    codec: str,
    configuration: bytes,
    samples: Sequence[Sequence[bytes]],
) -> str:
    """Hash configuration and three ordered packet windows, excluding timing/index/size."""
    if (channel, codec) not in {("audio", "aac"), ("video", "h264")}:
        raise ValueError("Unsupported stream codec/channel")
    if not configuration or len(configuration) > 65536 or len(samples) != 3:
        raise ValueError("Invalid stream configuration or samples")
    if any(not sample for sample in samples) or sum(len(sample) for sample in samples) > 20000:
        raise ValueError("Invalid sample packet count")
    payload_size = sum(len(packet) for sample in samples for packet in sample)
    if payload_size > MAX_PAYLOAD or any(not packet for sample in samples for packet in sample):
        raise ValueError("Invalid sample payload")
    algorithm = AUDIO_ALGORITHM if channel == "audio" else VIDEO_ALGORITHM
    digest = hashlib.sha256()
    digest.update(algorithm.encode("ascii") + b"\0")
    codec_bytes = codec.encode("ascii")
    for payload in (codec_bytes, configuration):
        digest.update(struct.pack(">I", len(payload)))
        digest.update(payload)
    digest.update(struct.pack(">I", 3))
    for sample in samples:
        digest.update(struct.pack(">I", len(sample)))
        for packet in sample:
            digest.update(struct.pack(">I", len(packet)))
            digest.update(packet)
    return digest.hexdigest()
