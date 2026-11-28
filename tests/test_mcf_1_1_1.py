"""Compatibility and export for Editarr's MCF 1.1.1 extension."""

import pytest

from scanner.mcf import (
    McfParseError,
    category_label,
    export_mcf_content,
    parse_mcf_content,
    parse_mcf_content_with_recovery,
)
from scanner.models import MCF_1_1_1_CATEGORIES

CLASSIFICATIONS = {
    "abuse": ("violence", "Abuse"),
    "gore": ("violence", "Gore"),
    "innuendo": ("language", "Innuendo"),
    "sexualRape": ("sex", "Rape"),
    "sizeism": ("discrimination", "Sizeism"),
    "ableism": ("discrimination", "Ableism"),
    "ageism": ("discrimination", "Ageism"),
    "other": ("dispensable_scenes", "Other"),
}


def _document(version: str, *tokens: str) -> bytes:
    metadata = "NOTE\nTITLE Example\n\n" if version != "1.0.0" else ""
    cues = "".join(f"{token}=medium=both\n" for token in tokens)
    return (
        f"WEBVTT MovieContentFilter {version}\n\n{metadata}"
        "NOTE\nSTART 00:00:00.000\nEND 00:00:30.000\n\n"
        f"00:00:10.000 --> 00:00:12.000\n{cues}\n"
    ).encode()


def test_extended_categories_round_trip_with_legacy_rape_and_metadata():
    assert MCF_1_1_1_CATEGORIES == CLASSIFICATIONS.keys()
    content = _document("1.1.1", "rape", *CLASSIFICATIONS)
    parsed = parse_mcf_content(content)
    assert parsed.source_version == "1.1.1"
    assert parsed.source_metadata.title == "Example"
    assert {
        entry.subcategory.value: (entry.category.value, category_label(entry.subcategory))
        for entry in parsed.entries
    } == {"rape": ("violence", "Rape"), **CLASSIFICATIONS}
    assert export_mcf_content(parsed.source_metadata, parsed.entries) == content

    recovered = parse_mcf_content_with_recovery(content)
    assert recovered.parsed == parsed
    assert not recovered.issues


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0"])
def test_earlier_versions_keep_legacy_rape_and_reject_extension_tokens(version):
    content = _document(version, "rape", *CLASSIFICATIONS)
    with pytest.raises(McfParseError, match="requires version 1.1.1"):
        parse_mcf_content(content)

    recovered = parse_mcf_content_with_recovery(content)
    (legacy,) = recovered.parsed.entries
    assert (legacy.category, legacy.subcategory) == ("violence", "rape")
    assert len(recovered.issues) == len(CLASSIFICATIONS)
    assert all("requires version 1.1.1" in issue.message for issue in recovered.issues)


def test_export_of_legacy_categories_keeps_the_1_1_0_header():
    parsed = parse_mcf_content(_document("1.1.1", "rape"))
    assert export_mcf_content(parsed.source_metadata, parsed.entries) == _document("1.1.0", "rape")
