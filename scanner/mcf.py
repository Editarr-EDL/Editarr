"""Public MCF parsing, recovery, and export API."""

# Preserve the parser import surface while implementations live by responsibility.
# ruff: noqa: F401

from scanner.formats.mcf_export import (
    _export_metadata_lines,
    _format_payload,
    _format_timestamp,
    _validate_export_entry,
    export_mcf_content,
)
from scanner.formats.mcf_parser import (
    _decode_lines,
    _parse_cues,
    _parse_material_bounds,
    _parse_metadata,
    _parse_metadata_value,
    _parse_payload,
    _parse_timestamp,
    _starts_metadata_block,
    _valid_range,
    parse_mcf_content,
)
from scanner.formats.mcf_recovery import (
    _parse_bounds_with_recovery,
    _parse_cues_with_recovery,
    _parse_metadata_with_recovery,
    _parse_recovery_header,
    _recovery_issue,
    parse_mcf_content_with_recovery,
)
from scanner.formats.mcf_types import (
    _HEADER,
    _HEADER_PREFIX,
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
    McfParseIssue,
    McfParseRecoveryResult,
    McfParseResult,
    TimedEntry,
    TimingInterval,
    _is_finite_number,
    _validate_interval,
    _validate_runtime,
    categories_for_family,
    category_family,
    category_label,
    map_affine_endpoint,
    map_affine_entries,
    map_affine_range,
)
