"""Bounded stream evidence and exact playback-relative timing comparisons."""

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from scanner.formats.community.stream_fingerprints import (
    AUDIO_ALGORITHM,
    PCM_AUDIO_ALGORITHM,
    VIDEO_ALGORITHM,
)


def rational(value: str) -> Fraction:
    """Parse bounded exact seconds, independent of local stream timebase."""
    if not isinstance(value, str) or not re.fullmatch(r"-?[0-9]{1,19}(?:/[0-9]{1,19})?", value):
        raise ValueError("Timing must be bounded rational seconds")
    numerator, _, denominator = value.partition("/")
    if abs(int(numerator)) > (1 << 63) - 1 or (
        denominator and not 0 < int(denominator) <= (1 << 63) - 1
    ):
        raise ValueError("Timing components must be signed-64-bit safe")
    return Fraction(value)


@dataclass(frozen=True)
class StreamEvidence:
    algorithm: str
    digest: str
    playback_start: str | None = None
    duration: str | None = None
    provenance: Literal["unknown", "selected", "analyzed"] = "unknown"
    description: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.algorithm):
            raise ValueError("Stream algorithm must be a bounded token")
        if not re.fullmatch(r"[a-f0-9]{64}", self.digest):
            raise ValueError("Stream digest must be SHA-256 hex")
        if self.playback_start is not None:
            rational(self.playback_start)
        if self.duration is not None and rational(self.duration) <= 0:
            raise ValueError("Stream duration must be positive")
        if self.provenance not in {"unknown", "selected", "analyzed"}:
            raise ValueError("Unknown stream provenance")
        if self.description is not None and (
            not isinstance(self.description, str) or len(self.description) > 512
        ):
            raise ValueError("Stream description must be bounded")


def compare_streams(
    channel: Literal["video", "audio"], left: StreamEvidence | None, right: StreamEvidence | None
) -> tuple[bool, list[str], list[str], list[str]]:
    """Return equality plus explanations; differing samples do not prove incompatibility."""
    reasons: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []
    algorithms = {AUDIO_ALGORITHM, PCM_AUDIO_ALGORITHM} if channel == "audio" else {VIDEO_ALGORITHM}
    supported = (
        left is not None
        and right is not None
        and left.algorithm == right.algorithm
        and left.algorithm in algorithms
    )
    equal = bool(supported and left.digest == right.digest)
    if equal:
        reasons.append(f"{channel}_stream_digest_match")
    else:
        missing.append(f"{channel}_stream_fingerprint")
        if supported:
            reasons.append(f"{channel}_stream_digest_differs")
    if any(item is not None and item.algorithm not in algorithms for item in (left, right)):
        reasons.append("unsupported_stream_fingerprint_ignored")
    timed = True
    for field in ("playback_start", "duration"):
        first = getattr(left, field, None)
        second = getattr(right, field, None)
        if first is None or second is None:
            timed = False
        else:
            if rational(first) != rational(second):
                conflicts.append(f"{channel}_stream_{field}")
    if timed and not conflicts:
        reasons.append(f"{channel}_stream_timing_match")
    if not timed:
        missing.append(f"{channel}_stream_timing")
    if channel == "audio" and (
        left is None
        or left.provenance == "unknown"
        or right is None
        or right.provenance != "analyzed"
    ):
        missing.append("analyzed_audio_stream")
    return equal, reasons, conflicts, missing
