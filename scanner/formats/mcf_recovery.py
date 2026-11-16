"""MCF parsing with recoverable issue reporting."""

from __future__ import annotations

from scanner.models import (
    McfSourceVersion,
    ScanMcfDocument,
)

from .mcf_parser import (
    _decode_lines,
    _parse_metadata_value,
    _parse_payload,
    _parse_timestamp,
    _starts_metadata_block,
    _valid_range,
)
from .mcf_types import (
    _HEADER,
    _METADATA_FIELDS,
    _TIMING,
    MAX_PAYLOAD_ENTRIES,
    McfCueEntry,
    McfParseError,
    McfParseIssue,
    McfParseRecoveryResult,
    McfParseResult,
)


def parse_mcf_content_with_recovery(
    content: bytes, *, duration_seconds: float | None = None
) -> McfParseRecoveryResult:
    """Parse valid MCF content while collecting and skipping recoverable errors."""
    lines = _decode_lines(content)
    source_version = _parse_recovery_header(lines)
    cursor = 2
    issues: list[McfParseIssue] = []
    metadata: dict[str, object] = {}
    if source_version == McfSourceVersion.V1_1_0 and _starts_metadata_block(lines, cursor):
        metadata, cursor = _parse_metadata_with_recovery(lines, cursor, issues)

    cursor, material_start, material_end, source_bounds_recovered = _parse_bounds_with_recovery(
        lines, cursor, duration_seconds, issues
    )
    entries = _parse_cues_with_recovery(lines, cursor, duration_seconds, issues)
    if source_bounds_recovered or material_start is None or material_end is None:
        raise McfParseError(
            "invalid_source_mcf_bounds",
            "MCF source START and END must be valid for synchronization",
        )
    source_metadata = ScanMcfDocument(
        source_version=source_version,
        material_start_seconds=material_start,
        material_end_seconds=material_end,
        **metadata,
    )
    return McfParseRecoveryResult(
        McfParseResult(source_version, source_metadata, entries),
        issues,
    )


def _parse_recovery_header(lines: list[str]) -> McfSourceVersion:
    """Validate the header before collecting recoverable body issues."""
    if not lines:
        raise McfParseError("invalid_mcf_document", "MCF header is required", 1)
    header_match = _HEADER.fullmatch(lines[0])
    if header_match is None:
        raise McfParseError("invalid_mcf_document", "MCF header is invalid", 1)
    try:
        source_version = McfSourceVersion(header_match.group(1))
    except ValueError as exc:
        raise McfParseError("unsupported_mcf_version", "MCF version is not supported", 1) from exc
    if len(lines) < 2 or lines[1] != "":
        raise McfParseError(
            "invalid_mcf_document", "MCF header must be followed by a blank line", 2
        )
    return source_version


def _recovery_issue(
    issues: list[McfParseIssue],
    lines: list[str],
    line_number: int,
    message: str,
    code: str = "invalid_mcf_document",
) -> None:
    """Append a line-aware issue for a skipped or repaired MCF field."""
    if len(issues) >= MAX_PAYLOAD_ENTRIES:
        raise McfParseError(
            "mcf_import_too_large",
            "MCF recoverable-issue limit exceeded",
            line_number,
        )
    issues.append(McfParseIssue(code, message, line_number, lines[line_number - 1]))


def _parse_metadata_with_recovery(
    lines: list[str], cursor: int, issues: list[McfParseIssue]
) -> tuple[dict[str, object], int]:
    """Read metadata while collecting invalid fields as issues."""
    field_names = dict(
        zip(
            _METADATA_FIELDS,
            (
                "title",
                "year",
                "work_type",
                "season",
                "episode",
                "source_url",
                "imdb_url",
                "release",
                "comment",
            ),
            strict=True,
        )
    )
    cursor += 1
    metadata: dict[str, object] = {}
    seen: set[str] = set()
    last_position = -1
    while cursor < len(lines) and lines[cursor] != "":
        line_number = cursor + 1
        field, separator, value = lines[cursor].partition(" ")
        if not separator or field not in field_names or not value:
            _recovery_issue(
                issues,
                lines,
                line_number,
                "MCF metadata field has no valid value",
                "invalid_mcf_metadata",
            )
            cursor += 1
            continue
        if field in seen:
            _recovery_issue(
                issues,
                lines,
                line_number,
                "MCF metadata field is duplicated",
                "invalid_mcf_metadata",
            )
            cursor += 1
            continue
        seen.add(field)
        position = _METADATA_FIELDS.index(field)
        if position <= last_position:
            _recovery_issue(
                issues,
                lines,
                line_number,
                "MCF metadata fields are out of order",
                "invalid_mcf_metadata",
            )
        last_position = max(last_position, position)
        try:
            metadata[field_names[field]] = _parse_metadata_value(field, value, line_number)
        except McfParseError as exc:
            _recovery_issue(issues, lines, line_number, exc.message, exc.code)
        cursor += 1
    if cursor >= len(lines):
        raise McfParseError(
            "invalid_mcf_metadata", "MCF metadata must end with a blank line", len(lines)
        )
    return metadata, cursor + 1


def _parse_bounds_with_recovery(
    lines: list[str],
    cursor: int,
    duration_seconds: float | None,
    issues: list[McfParseIssue],
) -> tuple[int, float | None, float | None, bool]:
    """Recover material bounds and flag any invalid source interval."""
    if cursor + 3 >= len(lines) or lines[cursor] != "NOTE" or lines[cursor + 3] != "":
        raise McfParseError(
            "invalid_source_mcf_bounds",
            "MCF source START and END must be valid for synchronization",
            cursor + 1,
        )
    values: list[float | None] = []
    recovered = False
    for offset, prefix in ((1, "START "), (2, "END ")):
        line_number = cursor + offset + 1
        line = lines[cursor + offset]
        if not line.startswith(prefix):
            _recovery_issue(
                issues, lines, line_number, f"MCF {prefix.strip()} timestamp is invalid"
            )
            values.append(None)
            recovered = True
            continue
        try:
            values.append(_parse_timestamp(line[len(prefix) :], line_number))
        except McfParseError as exc:
            _recovery_issue(issues, lines, line_number, exc.message, exc.code)
            values.append(None)
            recovered = True
    start, end = values
    if start is not None and end is not None and not _valid_range(start, end):
        _recovery_issue(issues, lines, cursor + 3, "MCF material bounds are invalid")
        end = None
        recovered = True
    if duration_seconds is not None and end is not None and end > duration_seconds:
        _recovery_issue(issues, lines, cursor + 3, "MCF material bounds exceed media duration")
        end = duration_seconds
        recovered = True
    return cursor + 4, start, end, recovered


def _parse_cues_with_recovery(
    lines: list[str],
    cursor: int,
    duration_seconds: float | None,
    issues: list[McfParseIssue],
) -> list[McfCueEntry]:
    """Keep valid cues while reporting malformed timings and payloads."""
    entries: list[McfCueEntry] = []
    cue_index = 0
    payload_entry_count = 0
    while cursor < len(lines):
        if lines[cursor] == "":
            _recovery_issue(
                issues,
                lines,
                cursor + 1,
                "MCF cue timing is invalid",
            )
            cursor += 1
            continue
        timing_line_number = cursor + 1
        timing = _TIMING.fullmatch(lines[cursor])
        if timing is None:
            _recovery_issue(issues, lines, timing_line_number, "MCF cue timing is invalid")
            while cursor < len(lines) and lines[cursor] != "":
                cursor += 1
            if cursor < len(lines):
                cursor += 1
            continue
        try:
            start = _parse_timestamp(timing.group(1), timing_line_number)
            end = _parse_timestamp(timing.group(2), timing_line_number)
        except McfParseError as exc:
            _recovery_issue(issues, lines, timing_line_number, exc.message, exc.code)
            start = end = None
        if start is not None and end is not None and not _valid_range(start, end):
            _recovery_issue(issues, lines, timing_line_number, "MCF cue range is invalid")
            start = end = None
        if duration_seconds is not None and end is not None and end > duration_seconds:
            _recovery_issue(issues, lines, timing_line_number, "MCF cue exceeds media duration")
            start = end = None
        cursor += 1
        payload_index = 0
        while cursor < len(lines) and lines[cursor] != "":
            if payload_entry_count >= MAX_PAYLOAD_ENTRIES:
                raise McfParseError(
                    "mcf_import_too_large", "MCF payload-entry limit exceeded", cursor + 1
                )
            payload_entry_count += 1
            if start is not None and end is not None:
                try:
                    entries.append(
                        _parse_payload(
                            lines[cursor], cursor + 1, cue_index, payload_index, start, end
                        )
                    )
                    payload_index += 1
                except McfParseError as exc:
                    _recovery_issue(issues, lines, cursor + 1, exc.message, exc.code)
            cursor += 1
        if payload_index == 0 and start is not None and end is not None:
            _recovery_issue(
                issues, lines, timing_line_number, "MCF cue has no valid payload entries"
            )
        if cursor < len(lines):
            cursor += 1
        cue_index += 1
    return entries
