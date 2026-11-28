"""Deterministic MCF serialization and payload validation."""

from __future__ import annotations

import math
from typing import Sequence

from scanner.models import (
    MCF_1_1_1_CATEGORIES,
    MCF_CATEGORY_TO_FAMILY,
    McfMetadata,
    McfSourceVersion,
    ReviewMcfDocument,
    ScanMcfDocument,
)

from .mcf_parser import _valid_range
from .mcf_types import (
    _HEADER_PREFIX,
    MAX_CONTENT_BYTES,
    MAX_LINE_CODEPOINTS,
    MAX_PAYLOAD_ENTRIES,
    McfCueEntry,
)


def export_mcf_content(
    metadata: McfMetadata | ReviewMcfDocument | ScanMcfDocument,
    entries: Sequence[McfCueEntry],
) -> bytes:
    """Export deterministic MCF using version 1.1.1 when a new category is required."""
    start = getattr(metadata, "material_start_seconds", None)
    end = getattr(metadata, "material_end_seconds", None)
    if start is None or end is None or not _valid_range(start, end):
        raise ValueError("MCF export requires valid material bounds")

    version = (
        McfSourceVersion.V1_1_1
        if any(entry.subcategory in MCF_1_1_1_CATEGORIES for entry in entries)
        else McfSourceVersion.V1_1_0
    )
    lines = [f"{_HEADER_PREFIX}{version.value}", ""]
    metadata_lines = _export_metadata_lines(metadata)
    if metadata_lines:
        lines.extend(["NOTE", *metadata_lines, ""])
    lines.extend(["NOTE", f"START {_format_timestamp(start)}", f"END {_format_timestamp(end)}", ""])

    grouped: dict[tuple[float, float], list[tuple[int, McfCueEntry]]] = {}
    if len(entries) > MAX_PAYLOAD_ENTRIES:
        raise ValueError("MCF payload-entry limit exceeded")
    for position, entry in enumerate(entries):
        _validate_export_entry(entry)
        grouped.setdefault((entry.start_seconds, entry.end_seconds), []).append((position, entry))
    ordered_groups = sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1], item[1][0][0])
    )
    for (cue_start, cue_end), payloads in ordered_groups:
        lines.append(f"{_format_timestamp(cue_start)} --> {_format_timestamp(cue_end)}")
        lines.extend(_format_payload(entry) for _position, entry in payloads)
        lines.append("")
    if any(len(line) > MAX_LINE_CODEPOINTS for line in lines):
        raise ValueError("MCF line exceeds the size limit")
    content = ("\n".join(lines) + "\n").encode("utf-8")
    if len(content) > MAX_CONTENT_BYTES:
        raise ValueError("MCF content exceeds the size limit")
    return content


def _format_timestamp(seconds: float) -> str:
    """Format finite seconds as an MCF timestamp with millisecond precision."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("MCF timestamp must be finite and non-negative")
    scaled = seconds * 1000
    if not math.isfinite(scaled):
        raise ValueError("MCF timestamp is too large")
    milliseconds = round(scaled)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _export_metadata_lines(metadata: McfMetadata) -> list[str]:
    """Serialize populated metadata fields in canonical MCF order."""
    values = (
        ("TITLE", metadata.title),
        ("YEAR", metadata.year),
        ("TYPE", metadata.work_type.value if metadata.work_type is not None else None),
        ("SEASON", metadata.season),
        ("EPISODE", metadata.episode),
        ("SOURCE", metadata.source_url),
        ("IMDB", metadata.imdb_url),
        ("RELEASE", metadata.release),
        ("COMMENT", metadata.comment),
    )
    lines: list[str] = []
    for field, value in values:
        if value is None:
            continue
        text = str(value)
        if text != text.strip() or not text or "\n" in text or "\r" in text:
            raise ValueError(f"MCF {field.lower()} is invalid")
        lines.append(f"{field} {text}")
    return lines


def _validate_export_entry(entry: McfCueEntry) -> None:
    """Reject cue entries with invalid timing or classification before export."""
    if not _valid_range(entry.start_seconds, entry.end_seconds):
        raise ValueError("MCF cue range is invalid")
    expected_family = MCF_CATEGORY_TO_FAMILY.get(entry.subcategory.value)
    if expected_family != entry.category:
        raise ValueError("MCF category pair is invalid")
    if entry.mcf_comment is not None and (
        not entry.mcf_comment
        or "\n" in entry.mcf_comment
        or "\r" in entry.mcf_comment
        or "-->" in entry.mcf_comment
    ):
        raise ValueError("MCF cue comment is invalid")


def _format_payload(entry: McfCueEntry) -> str:
    payload = f"{entry.subcategory.value}={entry.severity.value}={entry.mcf_channel.value}"
    if entry.mcf_comment is not None:
        payload += f" # {entry.mcf_comment}"
    return payload
