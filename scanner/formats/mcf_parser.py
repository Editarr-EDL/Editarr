"""Strict MCF parsing and field validation."""

from __future__ import annotations

import math
from urllib.parse import urlsplit

from scanner.models import (
    MAX_PERSISTED_INTEGER,
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfChannel,
    McfSourceVersion,
    McfWorkType,
    ScanMcfDocument,
    Severity,
)

from .mcf_types import (
    _HEADER,
    _METADATA_FIELDS,
    _NON_NEGATIVE_INTEGER,
    _POSITIVE_INTEGER,
    _TIMESTAMP,
    _TIMING,
    MAX_CONTENT_BYTES,
    MAX_LINE_CODEPOINTS,
    MAX_PAYLOAD_ENTRIES,
    McfCueEntry,
    McfParseError,
    McfParseResult,
)


def parse_mcf_content(content: bytes, *, duration_seconds: float | None = None) -> McfParseResult:
    """Parse a strict MCF 1.0.0 or 1.1.0 document without external side effects."""
    lines = _decode_lines(content)
    if not lines:
        raise McfParseError("invalid_mcf_document", "MCF header is required", 1)

    header_match = _HEADER.fullmatch(lines[0])
    if header_match is None:
        raise McfParseError("invalid_mcf_document", "MCF header is invalid", 1)
    version_value = header_match.group(1)
    try:
        source_version = McfSourceVersion(version_value)
    except ValueError as exc:
        raise McfParseError("unsupported_mcf_version", "MCF version is not supported", 1) from exc
    if len(lines) < 2 or lines[1] != "":
        raise McfParseError(
            "invalid_mcf_document", "MCF header must be followed by a blank line", 2
        )

    cursor = 2
    metadata: dict[str, object] = {}
    if source_version == McfSourceVersion.V1_1_0 and _starts_metadata_block(lines, cursor):
        metadata, cursor = _parse_metadata(lines, cursor)
    cursor, material_start, material_end = _parse_material_bounds(lines, cursor)
    if duration_seconds is not None and material_end > duration_seconds:
        raise McfParseError(
            "invalid_mcf_document",
            "MCF material bounds exceed media duration",
            cursor - 1,
        )
    entries = _parse_cues(lines, cursor, duration_seconds)
    source_metadata = ScanMcfDocument(
        source_version=source_version,
        material_start_seconds=material_start,
        material_end_seconds=material_end,
        **metadata,
    )
    return McfParseResult(source_version, source_metadata, entries)


def _decode_lines(content: bytes) -> list[str]:
    """Decode bounded UTF-8 content into lines, rejecting BOMs and oversized input."""
    if len(content) > MAX_CONTENT_BYTES:
        raise McfParseError("mcf_import_too_large", "MCF content exceeds the size limit")
    if content.startswith(b"\xef\xbb\xbf"):
        raise McfParseError("invalid_mcf_document", "MCF content must not contain a BOM", 1)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McfParseError("invalid_mcf_document", "MCF content must be valid UTF-8") from exc
    if "\r" in text.replace("\r\n", ""):
        raise McfParseError(
            "invalid_mcf_document", "MCF content contains an invalid line terminator"
        )
    if text and not (text.endswith("\n") or text.endswith("\r\n")):
        raise McfParseError("invalid_mcf_document", "MCF content must end with a line terminator")
    lines = text.replace("\r\n", "\n").split("\n")
    lines.pop()
    for line_number, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_CODEPOINTS:
            raise McfParseError(
                "mcf_import_too_large", "MCF line exceeds the size limit", line_number
            )
    return lines


def _starts_metadata_block(lines: list[str], cursor: int) -> bool:
    if cursor + 1 >= len(lines) or lines[cursor] != "NOTE":
        return False
    first_value = lines[cursor + 1]
    if first_value == "" or any(first_value.startswith(f"{field} ") for field in _METADATA_FIELDS):
        return True
    separator = cursor + 1
    while separator < len(lines) and lines[separator] != "":
        separator += 1
    return separator + 1 < len(lines) and lines[separator + 1] == "NOTE"


def _parse_metadata(lines: list[str], cursor: int) -> tuple[dict[str, object], int]:
    """Read strict metadata fields from the current line and return the new cursor."""
    cursor += 1
    metadata: dict[str, object] = {}
    last_position = -1
    field_names = {
        "TITLE": "title",
        "YEAR": "year",
        "TYPE": "work_type",
        "SEASON": "season",
        "EPISODE": "episode",
        "SOURCE": "source_url",
        "IMDB": "imdb_url",
        "RELEASE": "release",
        "COMMENT": "comment",
    }
    while cursor < len(lines) and lines[cursor] != "":
        line_number = cursor + 1
        line = lines[cursor]
        field, separator, value = line.partition(" ")
        if not separator or field not in field_names or not value:
            raise McfParseError(
                "invalid_mcf_metadata", "MCF metadata field is invalid", line_number
            )
        position = _METADATA_FIELDS.index(field)
        if position <= last_position:
            raise McfParseError(
                "invalid_mcf_metadata", "MCF metadata fields are out of order", line_number
            )
        last_position = position
        metadata[field_names[field]] = _parse_metadata_value(field, value, line_number)
        cursor += 1
    if cursor >= len(lines):
        raise McfParseError(
            "invalid_mcf_metadata", "MCF metadata must end with a blank line", len(lines)
        )
    return metadata, cursor + 1


def _parse_metadata_value(field: str, value: str, line_number: int) -> object:
    """Validate and convert one metadata field from its source line."""
    if value != value.strip():
        raise McfParseError("invalid_mcf_metadata", "MCF metadata value is invalid", line_number)
    if field == "YEAR":
        if not _NON_NEGATIVE_INTEGER.fullmatch(value):
            raise McfParseError(
                "invalid_mcf_metadata", "MCF year must be a non-negative integer", line_number
            )
        parsed = int(value)
        if parsed > MAX_PERSISTED_INTEGER:
            raise McfParseError("invalid_mcf_metadata", "MCF year is too large", line_number)
        return parsed
    if field in {"SEASON", "EPISODE"}:
        if not _POSITIVE_INTEGER.fullmatch(value):
            raise McfParseError(
                "invalid_mcf_metadata",
                "MCF season and episode must be positive integers",
                line_number,
            )
        parsed = int(value)
        if parsed > MAX_PERSISTED_INTEGER:
            raise McfParseError(
                "invalid_mcf_metadata",
                "MCF season or episode is too large",
                line_number,
            )
        return parsed
    if field == "TYPE":
        try:
            return McfWorkType(value)
        except ValueError as exc:
            raise McfParseError(
                "invalid_mcf_metadata", "MCF work type is invalid", line_number
            ) from exc
    if field in {"SOURCE", "IMDB"}:
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise McfParseError(
                "invalid_mcf_metadata", "MCF metadata URL is invalid", line_number
            ) from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McfParseError("invalid_mcf_metadata", "MCF metadata URL is invalid", line_number)
        if field == "IMDB" and not (
            parsed.hostname == "imdb.com" or (parsed.hostname or "").endswith(".imdb.com")
        ):
            raise McfParseError("invalid_mcf_metadata", "MCF IMDb URL host is invalid", line_number)
    return value


def _parse_material_bounds(lines: list[str], cursor: int) -> tuple[int, float, float]:
    """Read required START and END notes and return their bounds."""
    expected = ("NOTE", "START ", "END ", "")
    if cursor + 3 >= len(lines):
        raise McfParseError(
            "invalid_mcf_document", "MCF material bounds are incomplete", cursor + 1
        )
    for offset, prefix in enumerate(expected):
        line = lines[cursor + offset]
        matches = line == prefix if prefix in {"NOTE", ""} else line.startswith(prefix)
        if not matches:
            raise McfParseError(
                "invalid_mcf_document",
                "MCF material bounds section is invalid",
                cursor + offset + 1,
            )
    start = _parse_timestamp(lines[cursor + 1][6:], cursor + 2)
    end = _parse_timestamp(lines[cursor + 2][4:], cursor + 3)
    if not _valid_range(start, end):
        raise McfParseError("invalid_mcf_document", "MCF material bounds are invalid", cursor + 3)
    return cursor + 4, start, end


def _parse_cues(
    lines: list[str],
    cursor: int,
    duration_seconds: float | None,
) -> list[McfCueEntry]:
    """Parse ordered cue timings and payloads from the remaining lines."""
    entries: list[McfCueEntry] = []
    cue_index = 0
    while cursor < len(lines):
        timing_line_number = cursor + 1
        timing = _TIMING.fullmatch(lines[cursor])
        if timing is None:
            raise McfParseError(
                "invalid_mcf_document", "MCF cue timing is invalid", timing_line_number
            )
        start = _parse_timestamp(timing.group(1), timing_line_number)
        end = _parse_timestamp(timing.group(2), timing_line_number)
        if not _valid_range(start, end):
            raise McfParseError(
                "invalid_mcf_document", "MCF cue range is invalid", timing_line_number
            )
        if duration_seconds is not None and end > duration_seconds:
            raise McfParseError(
                "invalid_mcf_document", "MCF cue exceeds media duration", timing_line_number
            )
        cursor += 1
        payload_index = 0
        while cursor < len(lines) and lines[cursor] != "":
            if len(entries) >= MAX_PAYLOAD_ENTRIES:
                raise McfParseError(
                    "mcf_import_too_large", "MCF payload-entry limit exceeded", cursor + 1
                )
            entries.append(
                _parse_payload(lines[cursor], cursor + 1, cue_index, payload_index, start, end)
            )
            payload_index += 1
            cursor += 1
        if payload_index == 0:
            raise McfParseError(
                "invalid_mcf_document",
                "MCF cue must contain at least one payload entry",
                timing_line_number,
            )
        if cursor < len(lines):
            cursor += 1
        cue_index += 1
    return entries


def _parse_payload(
    line: str,
    line_number: int,
    cue_index: int,
    payload_index: int,
    start: float,
    end: float,
) -> McfCueEntry:
    """Validate one cue payload and build its categorized entry."""
    payload, marker, comment = line.partition(" # ")
    if marker and (not comment or "-->" in comment):
        raise McfParseError("invalid_mcf_document", "MCF cue comment is invalid", line_number)
    parts = payload.split("=")
    if len(parts) not in {2, 3} or any(not part for part in parts):
        raise McfParseError("invalid_mcf_document", "MCF cue payload is invalid", line_number)
    try:
        subcategory = McfCategory(parts[0])
        severity = Severity(parts[1])
        channel = McfChannel(parts[2]) if len(parts) == 3 else McfChannel.BOTH
    except ValueError as exc:
        raise McfParseError(
            "invalid_mcf_document", "MCF cue payload token is invalid", line_number
        ) from exc
    return McfCueEntry(
        cue_index=cue_index,
        payload_index=payload_index,
        line_number=line_number,
        start_seconds=start,
        end_seconds=end,
        category=MCF_CATEGORY_TO_FAMILY[subcategory.value],
        subcategory=subcategory,
        severity=severity,
        mcf_channel=channel,
        mcf_comment=comment if marker else None,
    )


def _parse_timestamp(value: str, line_number: int) -> float:
    """Convert a strict MCF timestamp to seconds with line-aware errors."""
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise McfParseError("invalid_mcf_document", "MCF timestamp is invalid", line_number)
    try:
        hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
        result = float(hours) * 3600 + minutes * 60 + seconds + milliseconds / 1000
        finite = math.isfinite(result)
    except OverflowError:
        finite = False
    if not finite:
        raise McfParseError("invalid_mcf_document", "MCF timestamp is not finite", line_number)
    return float(result)


def _valid_range(start: float, end: float) -> bool:
    return math.isfinite(start) and math.isfinite(end) and 0 <= start < end
