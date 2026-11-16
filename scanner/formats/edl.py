"""Pure Simple EDL normalization, export, and parsing helpers."""

import math
import re
import unicodedata
from dataclasses import dataclass

from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    EditAction,
    McfCategoryFamily,
    McfChannel,
    ScanFinding,
    Severity,
)

MAX_CONTENT_BYTES = 1_048_576
MAX_LINE_CODEPOINTS = 4_096
MAX_ACTION_ROWS = 10_000
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_LEGACY_CATEGORY_ALIASES = {
    "profanity": (McfCategoryFamily.LANGUAGE, "swearing"),
    "nudity": (McfCategoryFamily.NUDITY, "nudity"),
    "violence": (McfCategoryFamily.VIOLENCE, "violence"),
}


@dataclass(frozen=True)
class ParsedEdlFilter:
    source: str
    category: McfCategoryFamily | None
    subcategory: str | None
    severity: Severity | None
    mcf_channel: McfChannel | None
    mcf_comment: str | None
    label: str | None
    details: str | None
    group_key: str | None
    start_seconds: float
    end_seconds: float
    confidence: float | None
    edit_action: EditAction
    selected: bool = True
    line_number: int | None = None


@dataclass(frozen=True)
class ParsedEdl:
    filters: list[ParsedEdlFilter]
    ignored_action_count: int = 0


class EdlParseError(ValueError):
    """A safe, line-aware failure while parsing Simple EDL content."""

    def __init__(self, code: str, message: str, line_number: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line_number = line_number


@dataclass
class _RangeWithContributors:
    start: float
    end: float
    contributors: list[ScanFinding]


def export_edl_content(findings: list[ScanFinding]) -> str:
    """Return deterministic Simple EDL content with safe contributor comments."""
    skip_ranges, mute_ranges = _normalized_ranges_with_contributors(findings)
    contributor_order = {id(finding): position for position, finding in enumerate(findings)}
    events = [
        (item.start, item.end, 3, item.contributors)
        for item in skip_ranges
    ] + [
        (item.start, item.end, 1, item.contributors)
        for item in mute_ranges
    ]
    lines = ["## Editarr Simple EDL"]
    for start, end, action, contributors in sorted(
        events, key=lambda event: (event[0], event[1], event[2])
    ):
        lines.extend(
            _comment_lines(
                sorted(contributors, key=lambda finding: contributor_order[id(finding)]),
                action,
            )
        )
        lines.append(f"{start:.3f} {end:.3f} {action}")
    return "\n".join(lines)


def normalized_edl_ranges(
    findings: list[ScanFinding],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return EDL-equivalent (skip, mute) ranges for the selected findings."""
    skip, mute = _normalized_ranges_with_contributors(findings)
    return (
        [(item.start, item.end) for item in skip],
        [(item.start, item.end) for item in mute],
    )


def _normalized_ranges_with_contributors(
    findings: list[ScanFinding],
) -> tuple[list[_RangeWithContributors], list[_RangeWithContributors]]:
    """Clamp EDL ranges and retain the source contributors for each interval."""
    skip = _merge_ranges_with_contributors(
        [
            _finding_range_with_contributor(finding)
            for finding in findings
            if finding.edit_action == EditAction.SKIP
        ]
    )
    mute = _merge_ranges_with_contributors(
        [
            _finding_range_with_contributor(finding)
            for finding in findings
            if finding.edit_action == EditAction.MUTE
        ]
    )
    return skip, _subtract_ranges_with_contributors(mute, skip)


def _finding_range_with_contributor(finding: ScanFinding) -> _RangeWithContributors:
    start = max(0.0, finding.start_seconds)
    return _RangeWithContributors(
        start=start,
        end=max(start, finding.end_seconds or (start + 1.0)),
        contributors=[finding],
    )


def _merge_ranges_with_contributors(
    ranges: list[_RangeWithContributors],
) -> list[_RangeWithContributors]:
    """Merge touching intervals while combining their contributor identifiers."""
    merged: list[_RangeWithContributors] = []
    for current in sorted(ranges, key=lambda item: (item.start, item.end)):
        if merged and current.start <= merged[-1].end:
            previous = merged[-1]
            previous.end = max(previous.end, current.end)
            previous.contributors.extend(current.contributors)
        else:
            merged.append(
                _RangeWithContributors(current.start, current.end, list(current.contributors))
            )
    return merged


def _subtract_ranges_with_contributors(
    ranges: list[_RangeWithContributors], exclusions: list[_RangeWithContributors]
) -> list[_RangeWithContributors]:
    """Subtract excluded intervals while retaining each surviving range provenance."""
    remaining: list[_RangeWithContributors] = []
    for source in ranges:
        pieces = [(source.start, source.end)]
        for excluded in exclusions:
            next_pieces: list[tuple[float, float]] = []
            for start, end in pieces:
                if excluded.end <= start or excluded.start >= end:
                    next_pieces.append((start, end))
                    continue
                if start < excluded.start:
                    next_pieces.append((start, excluded.start))
                if excluded.end < end:
                    next_pieces.append((excluded.end, end))
            pieces = next_pieces
        remaining.extend(
            _RangeWithContributors(start, end, list(source.contributors))
            for start, end in pieces
        )
    return remaining


def _comment_lines(contributors: list[ScanFinding], action: int) -> list[str]:
    fallback = "Imported skip" if action == 3 else "Imported mute"
    lines: list[str] = []
    for finding in contributors:
        category = finding.subcategory.value if finding.subcategory is not None else "other"
        label = finding.label or fallback
        lines.append(f"## {_sanitize_comment(category)}: {_sanitize_comment(label)}")
    return lines


def _sanitize_comment(value: str) -> str:
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in str(value)
    )
    return " ".join(cleaned.split()).strip() or "other"


def parse_edl_content(content: bytes, *, duration_seconds: float | None = None) -> ParsedEdl:
    """Parse the bounded Editarr Simple EDL subset without external side effects."""
    if len(content) > MAX_CONTENT_BYTES:
        raise EdlParseError("edl_import_too_large", "EDL content exceeds the size limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EdlParseError(
            "invalid_edl_import", "EDL content must be valid UTF-8"
        ) from exc
    filters: list[ParsedEdlFilter] = []
    pending: list[tuple[str, str]] = []
    ignored = 0
    action_rows = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if len(raw_line.rstrip("\r\n")) > MAX_LINE_CODEPOINTS:
            raise EdlParseError(
                "edl_import_too_large", "EDL line exceeds the size limit", line_number
            )
        if not line:
            pending = []
            continue
        if line.startswith("##"):
            metadata = _metadata_comment(line[2:])
            if metadata is not None:
                pending.append(metadata)
            continue
        action_rows += 1
        if action_rows > MAX_ACTION_ROWS:
            raise EdlParseError(
                "edl_import_too_large", "EDL action-row limit exceeded", line_number
            )
        parts = line.split()
        if len(parts) != 3:
            raise EdlParseError(
                "invalid_edl_import",
                "EDL rows must contain exactly three fields",
                line_number,
            )
        start = _parse_number(parts[0], line_number, "start time")
        end = _parse_number(parts[1], line_number, "end time")
        try:
            action = int(parts[2])
        except ValueError as exc:
            raise EdlParseError(
                "invalid_edl_import", "EDL action must be an integer", line_number
            ) from exc
        if action not in (1, 3):
            ignored += 1
            pending = []
            continue
        if start < 0 or end <= start:
            raise EdlParseError(
                "invalid_edl_import", "EDL range is invalid", line_number
            )
        if duration_seconds is not None and end > duration_seconds:
            raise EdlParseError(
                "invalid_edl_import", "EDL range exceeds media duration", line_number
            )
        edit_action = EditAction.MUTE if action == 1 else EditAction.SKIP
        metadata = pending or [
            ("other", "Imported mute" if action == 1 else "Imported skip")
        ]
        filters.extend(
            ParsedEdlFilter(
                source="audio" if action == 1 else "visual",
                category=_classification_for_comment(category)[0],
                subcategory=_classification_for_comment(category)[1],
                severity=None,
                mcf_channel=None,
                mcf_comment=None,
                label=label,
                details=None,
                group_key=None,
                start_seconds=start,
                end_seconds=end,
                confidence=None,
                edit_action=edit_action,
                line_number=line_number,
            )
            for category, label in metadata
        )
        pending = []
    if not filters:
        raise EdlParseError(
            "no_supported_edl_actions", "No supported EDL actions were found"
        )
    return ParsedEdl(filters=filters, ignored_action_count=ignored)


def _metadata_comment(value: str) -> tuple[str, str] | None:
    if ":" not in value:
        return None
    category, label = (part.strip() for part in value.split(":", 1))
    if not category or not label:
        return None
    return category, label


def _classification_for_comment(
    category: str,
) -> tuple[McfCategoryFamily | None, str | None]:
    if category in MCF_CATEGORY_TO_FAMILY:
        return MCF_CATEGORY_TO_FAMILY[category], category
    return _LEGACY_CATEGORY_ALIASES.get(category, (None, None))


def _parse_number(value: str, line_number: int, field: str) -> float:
    if not _NUMBER.fullmatch(value):
        raise EdlParseError(
            "invalid_edl_import", f"EDL {field} is not a decimal number", line_number
        )
    number = float(value)
    if not math.isfinite(number):
        raise EdlParseError("invalid_edl_import", f"EDL {field} is not finite", line_number)
    return number
