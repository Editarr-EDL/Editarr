"""Community snapshot filters enter EDL, MCF and subtitle output through one rule."""

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.formats.edl import export_edl_content
from scanner.models import McfCategory, Severity
from scanner.persistence.repository.community_master_sources import ActiveMasterFilter
from scanner.persistence.repository.review_community_export import (
    edl_findings,
    exportable,
    mcf_entries,
    unusable_filter_ids,
)


def item(filter_id, start, end, *, category="violence", subcategory="punching", action="skip"):
    return ActiveMasterFilter(
        "link", filter_id, category, start, end, subcategory, "high", action, 0.5
    )


FILTERS = [
    item("inside", 10_000, 20_000),
    item("tail", 95_000, 130_000, action="mute"),
    item("after", 120_000, 125_000),
    item("wrong", 30_000, 31_000, subcategory="swearing"),
    item("word", 40_000, 41_000, category="language", subcategory="swearing", action="mute"),
]


def test_exports_clip_drop_misclassified_and_apply_enforced_severities():
    found = exportable(
        FILTERS, severities={McfCategory.punching: Severity.LOW}, duration_seconds=100
    )
    assert [(f.filter_id, f.start_ms, f.end_ms, f.severity) for f in found] == [
        ("inside", 10_000, 20_000, "low"),
        ("tail", 95_000, 100_000, "low"),
        ("word", 40_000, 41_000, "high"),
    ]
    assert unusable_filter_ids(FILTERS) == ["wrong"]
    edl = export_edl_content(edl_findings(found, 1))
    assert [line for line in edl.splitlines() if not line.startswith("#")] == [
        "10.000 20.000 3",
        "40.000 41.000 1",
        "95.000 100.000 1",
    ]
    entries = mcf_entries(found, 2)
    assert [(e.cue_index, e.subcategory, e.severity, e.mcf_channel.value) for e in entries] == [
        (2, McfCategory.punching, Severity.LOW, "both"),
        (3, McfCategory.punching, Severity.LOW, "audio"),
        (4, McfCategory.swearing, Severity.HIGH, "audio"),
    ]
