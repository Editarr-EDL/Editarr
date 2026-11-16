import pytest

from scanner.formats.edl import (
    MAX_ACTION_ROWS,
    MAX_CONTENT_BYTES,
    MAX_LINE_CODEPOINTS,
    EdlParseError,
    export_edl_content,
    normalized_edl_ranges,
    parse_edl_content,
)
from scanner.models import EditAction, McfCategoryFamily, ScanFinding


def scan_finding(
    identifier: int,
    action: EditAction,
    start: float,
    end: float,
) -> ScanFinding:
    return ScanFinding(
        id=identifier,
        scan_id="scan-1",
        category=None,
        subcategory=None,
        start_seconds=start,
        end_seconds=end,
        confidence=1,
        source="test",
        selected=True,
        edit_action=action,
    )


def test_editarr_simple_edl_handles_empty_mute_skip_and_overlap_cases():
    assert export_edl_content([]) == "## Editarr Simple EDL"

    mute_only = export_edl_content([scan_finding(1, EditAction.MUTE, 2, 4)])
    assert mute_only == "## Editarr Simple EDL\n## other: Imported mute\n2.000 4.000 1"

    skip_only = export_edl_content([scan_finding(2, EditAction.SKIP, 5, 8)])
    assert skip_only == "## Editarr Simple EDL\n## other: Imported skip\n5.000 8.000 3"

    mixed_overlap = export_edl_content(
        [
            scan_finding(3, EditAction.MUTE, 0, 10),
            scan_finding(4, EditAction.SKIP, 3, 7),
            scan_finding(5, EditAction.MUTE, 8, 12),
        ]
    )
    assert mixed_overlap == (
        "## Editarr Simple EDL\n"
        "## other: Imported mute\n"
        "## other: Imported mute\n"
        "0.000 3.000 1\n"
        "## other: Imported skip\n"
        "3.000 7.000 3\n"
        "## other: Imported mute\n"
        "## other: Imported mute\n"
        "7.000 12.000 1"
    )


def test_normalized_ranges_match_the_mute_windows_exported_to_the_edl():
    skip_ranges, mute_ranges = normalized_edl_ranges(
        [
            scan_finding(1, EditAction.MUTE, 1, 3),
            scan_finding(2, EditAction.MUTE, 2, 4),
            scan_finding(3, EditAction.SKIP, 2.5, 3.5),
        ]
    )

    assert skip_ranges == [(2.5, 3.5)]
    assert mute_ranges == [(1, 2.5), (3.5, 4)]


def test_merged_comments_preserve_original_filter_order():
    later = scan_finding(1, EditAction.SKIP, 2, 5)
    later.label = "First in review"
    earlier = scan_finding(2, EditAction.SKIP, 1, 3)
    earlier.label = "Second in review"

    exported = export_edl_content([later, earlier])

    assert exported == (
        "## Editarr Simple EDL\n"
        "## other: First in review\n"
        "## other: Second in review\n"
        "1.000 5.000 3"
    )


def test_parser_imports_metadata_and_fallbacks_with_ignored_actions():
    parsed = parse_edl_content(
        b"\xef\xbb\xbf## Editarr Simple EDL\r\n"
        b"## profanity: word\r\n"
        b"1.000 2.500 1\r\n"
        b"7.000 8.000 2\r\n"
        b"9.000 10.000 3\r\n"
    )
    assert parsed.ignored_action_count == 1
    assert [
        (item.source, item.category, item.subcategory, item.label, item.edit_action)
        for item in parsed.filters
    ] == [
        (
            "audio",
            McfCategoryFamily.LANGUAGE,
            "swearing",
            "word",
            EditAction.MUTE,
        ),
        ("visual", None, None, "Imported skip", EditAction.SKIP),
    ]


def test_parser_ignores_unsupported_actions_after_syntax_and_number_validation():
    parsed = parse_edl_content(b"-2 -1 2\n1 2 1\n")

    assert parsed.ignored_action_count == 1
    assert [(item.start_seconds, item.end_seconds) for item in parsed.filters] == [(1, 2)]


def test_parser_rejects_malformed_rows_limits_and_no_supported_actions():
    with pytest.raises(EdlParseError) as error:
        parse_edl_content(b"## header\n1.0 2.0 nope\n")
    assert error.value.code == "invalid_edl_import"
    assert error.value.line_number == 2
    with pytest.raises(EdlParseError) as error:
        parse_edl_content(b"0 1 2\n")
    assert error.value.code == "no_supported_edl_actions"
    with pytest.raises(EdlParseError) as error:
        parse_edl_content((b"x" * 4097) + b"\n1 2 1\n")
    assert error.value.code == "edl_import_too_large"


def test_parser_accepts_unicode_and_duration_boundaries():
    parsed = parse_edl_content("## other: Привет\n0 2 1\n".encode(), duration_seconds=2)
    assert parsed.filters[0].label == "Привет"
    with pytest.raises(EdlParseError):
        parse_edl_content(b"0 2.001 1\n", duration_seconds=2)


def test_parser_rejects_invalid_utf8_and_exact_resource_limit_overflows():
    with pytest.raises(EdlParseError, match="valid UTF-8") as error:
        parse_edl_content(b"\xff\n")
    assert error.value.code == "invalid_edl_import"
    assert error.value.line_number is None

    with pytest.raises(EdlParseError) as error:
        parse_edl_content(b"#" * (MAX_CONTENT_BYTES + 1))
    assert error.value.code == "edl_import_too_large"

    with pytest.raises(EdlParseError) as error:
        parse_edl_content((b"#" * (MAX_LINE_CODEPOINTS + 1)) + b"\n1 2 1\n")
    assert error.value.code == "edl_import_too_large"
    assert error.value.line_number == 1

    action_rows = b"0 1 2\n" * MAX_ACTION_ROWS + b"1 2 1\n"
    with pytest.raises(EdlParseError) as error:
        parse_edl_content(action_rows)
    assert error.value.code == "edl_import_too_large"
    assert error.value.line_number == MAX_ACTION_ROWS + 1


def test_export_sanitizes_comment_injection_and_round_trips_ranges():
    mute = scan_finding(1, EditAction.MUTE, 1, 2)
    mute.label = "line one\n3 4 3\u200b"
    skip = scan_finding(2, EditAction.SKIP, 4, 6)
    skip.label = "scene\r\n7 8 1"

    exported = export_edl_content([mute, skip])

    assert "\n3 4 3\n" not in exported
    assert "\n7 8 1\n" not in exported
    assert "\u200b" not in exported
    parsed = parse_edl_content(exported.encode())
    assert [
        (item.start_seconds, item.end_seconds, item.edit_action)
        for item in parsed.filters
    ] == [
        (1.0, 2.0, EditAction.MUTE),
        (4.0, 6.0, EditAction.SKIP),
    ]
