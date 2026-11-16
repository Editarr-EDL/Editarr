from pathlib import Path

import pytest

from scanner.mcf import (
    MAX_CONTENT_BYTES,
    MAX_LINE_CODEPOINTS,
    MAX_PAYLOAD_ENTRIES,
    McfCueEntry,
    McfParseError,
    categories_for_family,
    category_family,
    category_label,
    export_mcf_content,
    parse_mcf_content,
    parse_mcf_content_with_recovery,
)
from scanner.models import (
    MCF_CATEGORY_LABELS,
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewMcfDocument,
    Severity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"


def test_parse_and_export_mcf_1_1_golden_bytes():
    content = (FIXTURES / "valid-1.1.mcf").read_bytes()

    parsed = parse_mcf_content(content)

    assert parsed.source_version.value == "1.1.0"
    assert parsed.source_metadata.title == "Ozymandias"
    assert [
        (entry.category, entry.severity, entry.mcf_channel, entry.mcf_comment)
        for entry in parsed.entries
    ] == [
        (McfCategoryFamily.VIOLENCE, Severity.HIGH, McfChannel.BOTH, None),
        (McfCategoryFamily.DRUGS, Severity.MEDIUM, McfChannel.BOTH, "Some comment"),
        (McfCategoryFamily.DRUGS, Severity.HIGH, McfChannel.VIDEO, None),
    ]
    assert export_mcf_content(parsed.source_metadata, parsed.entries) == (
        FIXTURES / "exported-1.1.mcf"
    ).read_bytes()


def test_parse_mcf_1_0_crlf_and_bounds_only_documents():
    source_fixture = parse_mcf_content((FIXTURES / "valid-1.0.mcf").read_bytes())
    assert source_fixture.source_version.value == "1.0.0"
    assert len(source_fixture.entries) == 3

    parsed = parse_mcf_content(
        b"WEBVTT MovieContentFilter 1.0.0\r\n"
        b"\r\n"
        b"NOTE\r\n"
        b"START 00:00:00.000\r\n"
        b"END 00:01:00.000\r\n"
        b"\r\n"
    )

    assert parsed.source_version.value == "1.0.0"
    assert parsed.source_metadata.title is None
    assert parsed.entries == []

    empty_metadata = parse_mcf_content(
        b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 00:01:00.000\n\n"
    )
    assert empty_metadata.entries == []


@pytest.mark.parametrize("token", list(McfCategory))
def test_all_category_tokens_parse_and_registry_helpers_are_canonical(token):
    content = (
        "WEBVTT MovieContentFilter 1.0.0\n\n"
        "NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
        f"00:00:00.000 --> 00:00:01.000\n{token.value}=low=audio\n"
    ).encode()

    entry = parse_mcf_content(content).entries[0]

    assert entry.subcategory == token
    assert entry.category == MCF_CATEGORY_TO_FAMILY[token.value]
    assert category_family(token) == entry.category
    assert category_family(token.value) == entry.category
    assert category_label(token) == MCF_CATEGORY_LABELS[token.value]
    assert (token, category_label(token)) in categories_for_family(entry.category)


def test_export_groups_only_equal_ranges_and_uses_review_order_for_payloads():
    metadata = ReviewMcfDocument(
        material_start_seconds=4.02,
        material_end_seconds=100.1,
    )
    entries = [
        _entry(0, 0, 6, 10, McfCategory.swearing, Severity.HIGH),
        _entry(1, 0, 5, 11, McfCategory.violence, Severity.MEDIUM),
        _entry(2, 0, 6, 10, McfCategory.nudity, Severity.LOW),
        _entry(3, 0, 6, 11, McfCategory.fear, Severity.LOW),
    ]

    exported = export_mcf_content(metadata, entries).decode()

    assert exported == (
        "WEBVTT MovieContentFilter 1.1.0\n\n"
        "NOTE\nSTART 00:00:04.020\nEND 00:01:40.100\n\n"
        "00:00:05.000 --> 00:00:11.000\nviolence=medium=both\n\n"
        "00:00:06.000 --> 00:00:10.000\n"
        "swearing=high=both\nnudity=low=both\n\n"
        "00:00:06.000 --> 00:00:11.000\nfear=low=both\n\n"
    )


@pytest.mark.parametrize(
    ("content", "code", "line_number"),
    [
        (b"\xef\xbb\xbfWEBVTT MovieContentFilter 1.1.0\n", "invalid_mcf_document", 1),
        (b"WEBVTT MovieContentFilter 2.0.0\n\n", "unsupported_mcf_version", 1),
        (
            b"WEBVTT MovieContentFilter 1.1.0 extra\n\n",
            "invalid_mcf_document",
            1,
        ),
        (b"WEBVTT\n\n", "invalid_mcf_document", 1),
        (b"WEBVTT MovieContentFilter 1.0.0\nNOTE\n", "invalid_mcf_document", 2),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\nNOTE\nTITLE Nope\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n",
            "invalid_mcf_document",
            4,
        ),
        (
            b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nYEAR 2020\nTITLE Nope\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n",
            "invalid_mcf_metadata",
            5,
        ),
        (
            b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nSOURCE https://[bad\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n",
            "invalid_mcf_metadata",
            4,
        ),
        (
            b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nUNKNOWN value\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n",
            "invalid_mcf_metadata",
            4,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00\nEND 00:00:02.000\n\n",
            "invalid_mcf_document",
            4,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:02.000\nEND 00:00:02.000\n\n",
            "invalid_mcf_document",
            5,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000 align:start\nviolence=high\n",
            "invalid_mcf_document",
            7,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\nViolence=high\n",
            "invalid_mcf_document",
            8,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\nviolence=critical\n",
            "invalid_mcf_document",
            8,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\nviolence=high=Both\n",
            "invalid_mcf_document",
            8,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\nviolence=high # bad --> comment\n",
            "invalid_mcf_document",
            8,
        ),
        (
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\n",
            "invalid_mcf_document",
            7,
        ),
    ],
)
def test_parser_rejects_invalid_mcf_subset(content, code, line_number):
    with pytest.raises(McfParseError) as error:
        parse_mcf_content(content)

    assert error.value.code == code
    assert error.value.line_number == line_number
    assert bytes(content[:20]).decode("utf-8", "ignore") not in error.value.message


def test_parser_enforces_utf8_duration_and_resource_limits():
    with pytest.raises(McfParseError, match="valid UTF-8"):
        parse_mcf_content(b"\xff")
    with pytest.raises(McfParseError) as error:
        parse_mcf_content(b"#" * (MAX_CONTENT_BYTES + 1))
    assert error.value.code == "mcf_import_too_large"
    with pytest.raises(McfParseError) as error:
        parse_mcf_content(
            b"WEBVTT MovieContentFilter 1.0.0\n\nNOTE\nSTART "
            + b"0" * MAX_LINE_CODEPOINTS
            + b"\nEND 00:00:02.000\n\n"
        )
    assert error.value.code == "mcf_import_too_large"
    assert error.value.line_number == 4

    payloads = b"violence=low\n" * (MAX_PAYLOAD_ENTRIES + 1)
    with pytest.raises(McfParseError) as error:
        parse_mcf_content(
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 01:00:00.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\n" + payloads
        )
    assert error.value.code == "mcf_import_too_large"
    assert error.value.line_number == 8 + MAX_PAYLOAD_ENTRIES

    with pytest.raises(McfParseError, match="media duration") as error:
        parse_mcf_content(
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:03.000\n\n",
            duration_seconds=2,
        )
    assert error.value.line_number == 5

    huge_hours = "9" * 4_000
    with pytest.raises(McfParseError, match="not finite") as error:
        parse_mcf_content(
            (
                "WEBVTT MovieContentFilter 1.0.0\n\n"
                "NOTE\nSTART 00:00:00.000\n"
                f"END {huge_hours}:00:00.000\n\n"
            ).encode()
        )
    assert error.value.line_number == 5

    huge_year = "9" * 4_000
    with pytest.raises(McfParseError, match="year is too large") as error:
        parse_mcf_content(
            (
                "WEBVTT MovieContentFilter 1.1.0\n\n"
                f"NOTE\nYEAR {huge_year}\n\n"
                "NOTE\nSTART 00:00:00.000\nEND 00:00:01.000\n\n"
            ).encode()
        )
    assert error.value.code == "invalid_mcf_metadata"
    assert error.value.line_number == 4


@pytest.mark.parametrize(
    "content",
    [
        "WEBVTT MovieContentFilter \u0661.0.0\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:01.000\n\n",
        "WEBVTT MovieContentFilter 1.0.0\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:0\u0661.000\n\n",
        "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nYEAR \u0661\u0669\u0669\u0669\n\n"
        "NOTE\nSTART 00:00:00.000\nEND 00:00:01.000\n\n",
    ],
)
def test_parser_accepts_only_ascii_digits(content):
    with pytest.raises(McfParseError):
        parse_mcf_content(content.encode())


def test_recovery_parser_collects_issues_and_keeps_valid_content():
    content = (
        b"WEBVTT MovieContentFilter 1.1.0\n\n"
        b"NOTE\nTITLE Movie\nTYPE movie\nSEASON \nEPISODE \nRELEASE \nCOMMENT \n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 99:59:59.999\n\n"
        b"00:00:01.000 --> 00:00:02.000\nviolence=low=both\n\n"
        b"02:00:00.000 --> 02:00:01.000\nswearing=low=audio\n"
    )

    recovered = parse_mcf_content_with_recovery(content)

    assert recovered.parsed.source_metadata.title == "Movie"
    assert recovered.parsed.source_metadata.season is None
    assert recovered.parsed.source_metadata.material_end_seconds == 359999.999
    assert [entry.subcategory for entry in recovered.parsed.entries] == [
        McfCategory.violence,
        McfCategory.swearing,
    ]
    assert [(issue.line_number, issue.line_text) for issue in recovered.issues] == [
        (6, "SEASON "),
        (7, "EPISODE "),
        (8, "RELEASE "),
        (9, "COMMENT "),
    ]


def test_recovery_parser_reports_out_of_order_metadata_but_keeps_values():
    recovered = parse_mcf_content_with_recovery(
        b"WEBVTT MovieContentFilter 1.1.0\n\n"
        b"NOTE\nYEAR 2015\nTITLE Movie\n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
    )

    assert recovered.parsed.source_metadata.title == "Movie"
    assert recovered.parsed.source_metadata.year == 2015
    assert [(issue.message, issue.line_number) for issue in recovered.issues] == [
        ("MCF metadata fields are out of order", 5)
    ]


def test_recovery_parser_can_skip_an_unknown_first_metadata_field():
    recovered = parse_mcf_content_with_recovery(
        b"WEBVTT MovieContentFilter 1.1.0\n\n"
        b"NOTE\nUNKNOWN value\nTITLE Movie\n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
    )

    assert recovered.parsed.source_metadata.title == "Movie"
    assert [(issue.code, issue.line_number) for issue in recovered.issues] == [
        ("invalid_mcf_metadata", 4)
    ]


def test_recovery_parser_reports_extra_blank_cue_lines_instead_of_silently_relaxing():
    recovered = parse_mcf_content_with_recovery(
        b"WEBVTT MovieContentFilter 1.0.0\n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
        b"\n"
        b"00:00:00.000 --> 00:00:01.000\nviolence=high\n"
    )

    assert [entry.subcategory for entry in recovered.parsed.entries] == [
        McfCategory.violence
    ]
    assert [(issue.message, issue.line_number) for issue in recovered.issues] == [
        ("MCF cue timing is invalid", 7)
    ]


def test_recovery_parser_bounds_invalid_payload_and_issue_amplification():
    payloads = b"invalid\n" * (MAX_PAYLOAD_ENTRIES + 1)

    with pytest.raises(McfParseError) as error:
        parse_mcf_content_with_recovery(
            b"WEBVTT MovieContentFilter 1.0.0\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 01:00:00.000\n\n"
            b"00:00:00.000 --> 00:00:01.000\n" + payloads
        )

    assert error.value.code == "mcf_import_too_large"
    assert error.value.line_number == 8 + MAX_PAYLOAD_ENTRIES


def test_export_rejects_invalid_pairs_comments_ranges_and_limits():
    metadata = ReviewMcfDocument(
        material_start_seconds=0,
        material_end_seconds=10,
    )
    invalid_pair = _entry(0, 0, 1, 2, McfCategory.violence, Severity.HIGH)
    object.__setattr__(invalid_pair, "category", McfCategoryFamily.LANGUAGE)
    with pytest.raises(ValueError, match="category pair"):
        export_mcf_content(metadata, [invalid_pair])

    invalid_comment = _entry(0, 0, 1, 2, McfCategory.violence, Severity.HIGH)
    object.__setattr__(invalid_comment, "mcf_comment", "bad --> comment")
    with pytest.raises(ValueError, match="comment"):
        export_mcf_content(metadata, [invalid_comment])

    invalid_range = _entry(0, 0, 2, 1, McfCategory.violence, Severity.HIGH)
    with pytest.raises(ValueError, match="range"):
        export_mcf_content(metadata, [invalid_range])

    entry = _entry(0, 0, 1, 2, McfCategory.violence, Severity.HIGH)
    with pytest.raises(ValueError, match="payload-entry limit"):
        export_mcf_content(metadata, [entry] * (MAX_PAYLOAD_ENTRIES + 1))

    with pytest.raises(ValueError, match="timestamp is too large"):
        export_mcf_content(
            ReviewMcfDocument(
                material_start_seconds=0,
                material_end_seconds=1e308,
            ),
            [],
        )


def _entry(
    cue_index,
    payload_index,
    start,
    end,
    subcategory,
    severity,
    *,
    channel=McfChannel.BOTH,
    comment=None,
):
    return McfCueEntry(
        cue_index=cue_index,
        payload_index=payload_index,
        line_number=1,
        start_seconds=start,
        end_seconds=end,
        category=MCF_CATEGORY_TO_FAMILY[subcategory.value],
        subcategory=subcategory,
        severity=severity,
        mcf_channel=channel,
        mcf_comment=comment,
    )
