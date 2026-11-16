"""Pure bounded Movie Content Filter parsing and export helpers."""

import math
import re
from dataclasses import dataclass
from typing import Sequence, TypeVar

from scanner.models import (
    MCF_CATEGORY_LABELS,
    MCF_CATEGORY_REGISTRY,
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    NewManualReviewFilter,
    ReviewFilterInput,
    ScanMcfDocument,
    Severity,
)

MAX_CONTENT_BYTES = 1_048_576
MAX_LINE_CODEPOINTS = 4_096
MAX_PAYLOAD_ENTRIES = 10_000

_HEADER_PREFIX = "WEBVTT MovieContentFilter "
_HEADER = re.compile(r"^WEBVTT MovieContentFilter (\d+\.\d+\.\d+)$", re.ASCII)
_TIMESTAMP = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})$", re.ASCII)
_TIMING = re.compile(
    r"^(\d{2,}:[0-5]\d:[0-5]\d\.\d{3}) --> "
    r"(\d{2,}:[0-5]\d:[0-5]\d\.\d{3})$",
    re.ASCII,
)
_NON_NEGATIVE_INTEGER = re.compile(r"^(?:0|[1-9]\d*)$", re.ASCII)
_POSITIVE_INTEGER = re.compile(r"^[1-9]\d*$", re.ASCII)
_METADATA_FIELDS = (
    "TITLE",
    "YEAR",
    "TYPE",
    "SEASON",
    "EPISODE",
    "SOURCE",
    "IMDB",
    "RELEASE",
    "COMMENT",
)


class McfParseError(ValueError):
    """A safe, stable, line-aware MCF parsing failure."""

    def __init__(self, code: str, message: str, line_number: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line_number = line_number


@dataclass(frozen=True)
class McfCueEntry:
    cue_index: int
    payload_index: int
    line_number: int
    start_seconds: float
    end_seconds: float
    category: McfCategoryFamily
    subcategory: McfCategory
    severity: Severity
    mcf_channel: McfChannel
    mcf_comment: str | None = None


@dataclass(frozen=True)
class McfParseResult:
    source_version: McfSourceVersion
    source_metadata: ScanMcfDocument
    entries: list[McfCueEntry]


@dataclass(frozen=True)
class McfParseIssue:
    code: str
    message: str
    line_number: int
    line_text: str


@dataclass(frozen=True)
class McfParseRecoveryResult:
    parsed: McfParseResult
    issues: list[McfParseIssue]


@dataclass(frozen=True)
class TimingInterval:
    start_seconds: float
    end_seconds: float


TimedEntry = TypeVar("TimedEntry", ReviewFilterInput, NewManualReviewFilter)


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_interval(interval: TimingInterval, label: str) -> None:
    if not _is_finite_number(interval.start_seconds) or not _is_finite_number(interval.end_seconds):
        raise ValueError(f"{label} interval must be finite")
    if interval.start_seconds < 0 or interval.end_seconds <= interval.start_seconds:
        raise ValueError(f"{label} interval must be non-negative and ordered")


def _validate_runtime(duration_seconds: float | None, target: TimingInterval) -> None:
    if duration_seconds is None:
        return
    if not _is_finite_number(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Media runtime must be finite and positive")
    if target.end_seconds > duration_seconds:
        raise ValueError("Target interval exceeds media runtime")


def map_affine_endpoint(
    endpoint_seconds: float,
    source: TimingInterval,
    target: TimingInterval,
    *,
    duration_seconds: float | None = None,
) -> float:
    """Map one endpoint between intervals without clamping or rounding."""
    if not _is_finite_number(endpoint_seconds):
        raise ValueError("Cue endpoint must be finite")
    _validate_interval(source, "Source")
    _validate_interval(target, "Target")
    _validate_runtime(duration_seconds, target)
    if endpoint_seconds < 0:
        raise ValueError("Cue endpoint must be non-negative")
    mapped = target.start_seconds + (
        (endpoint_seconds - source.start_seconds) / (source.end_seconds - source.start_seconds)
    ) * (target.end_seconds - target.start_seconds)
    if not math.isfinite(mapped):
        raise ValueError("Mapped cue endpoint must be finite")
    if mapped < 0 or (duration_seconds is not None and mapped > duration_seconds):
        raise ValueError("Mapped cue endpoint is outside the media runtime")
    return mapped


def map_affine_range(
    start_seconds: float,
    end_seconds: float | None,
    source: TimingInterval,
    target: TimingInterval,
    *,
    duration_seconds: float | None = None,
) -> tuple[float, float | None]:
    """Map a complete cue range, preserving a missing END."""
    if not _is_finite_number(start_seconds) or (
        end_seconds is not None and not _is_finite_number(end_seconds)
    ):
        raise ValueError("Cue range must be finite")
    _validate_interval(source, "Source")
    _validate_interval(target, "Target")
    _validate_runtime(duration_seconds, target)
    if start_seconds < 0 or (end_seconds is not None and end_seconds <= start_seconds):
        raise ValueError("Cue range must be non-negative and ordered")
    mapped_start = map_affine_endpoint(
        start_seconds, source, target, duration_seconds=duration_seconds
    )
    mapped_end = (
        map_affine_endpoint(end_seconds, source, target, duration_seconds=duration_seconds)
        if end_seconds is not None
        else None
    )
    if mapped_end is not None and mapped_end <= mapped_start:
        raise ValueError("Mapped cue range must be ordered")
    return mapped_start, mapped_end


def map_affine_entries(
    entries: Sequence[TimedEntry],
    source: TimingInterval,
    target: TimingInterval,
    *,
    duration_seconds: float | None = None,
) -> list[TimedEntry]:
    """Map ordered review entries while preserving every non-timing field."""
    mapped: list[TimedEntry] = []
    for entry in entries:
        start_seconds, end_seconds = map_affine_range(
            entry.start_seconds,
            entry.end_seconds,
            source,
            target,
            duration_seconds=duration_seconds,
        )
        mapped.append(
            entry.model_copy(
                update={
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                }
            )
        )
    return mapped


def category_family(token: str | McfCategory) -> McfCategoryFamily | None:
    """Return the canonical family for a category token, if it is known."""
    value = token.value if isinstance(token, McfCategory) else token
    return MCF_CATEGORY_TO_FAMILY.get(value)


def category_label(token: str | McfCategory) -> str | None:
    """Return the official display label for a category token, if it is known."""
    value = token.value if isinstance(token, McfCategory) else token
    return MCF_CATEGORY_LABELS.get(value)


def categories_for_family(
    family: McfCategoryFamily,
) -> tuple[tuple[McfCategory, str], ...]:
    """Return the ordered category tokens and labels belonging to a family."""
    return tuple((McfCategory(token), label) for token, label in MCF_CATEGORY_REGISTRY[family])
