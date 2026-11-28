"""Standalone .skp serialization against the review model and the importer."""

import pytest

from scanner.formats.videoskip import MAX_TEXT, parse_videoskip
from scanner.formats.videoskip_export import VideoSkipExportIssue, export_videoskip
from scanner.models import ReviewFilter


def _cue(filter_id="cue", **changes):
    fields = {
        "id": filter_id,
        "source_scan_id": "scan",
        "source": "visual",
        "category": "violence",
        "subcategory": "violence",
        "severity": "high",
        "label": None,
        "details": None,
        "group_key": None,
        "start_seconds": 5.125,
        "end_seconds": 7.250,
        "confidence": None,
        "edit_action": "skip",
        "selected": True,
    }
    return ReviewFilter(**(fields | changes))


def test_export_writes_sorted_two_line_cues_with_selected_overlaps_and_optional_sync():
    result = export_videoskip(
        [
            _cue("later", label="Fight"),
            _cue(
                "mute",
                category="language",
                subcategory="swearing",
                severity="low",
                start_seconds=3.020,
                end_seconds=5.775,
                edit_action="mute",
                details="A short phrase",
            ),
            _cue("excluded", label="Never written", selected=False),
        ],
        reference_seconds=2.015,
        reference_description="Logo first appears",
    )
    assert result.exported_count == 2
    assert result.issues == ()
    assert result.content == (
        b"0:00:02.015\nLogo first appears\n\n"
        b"0:00:03.020 --> 0:00:05.775\nprofanity audio 1 (A short phrase)\n\n"
        b"0:00:05.125 --> 0:00:07.250\nviolence 3 (Fight)\n\n"
    )
    parsed = parse_videoskip(result.content)
    assert parsed.issues == []
    assert (parsed.sync.reference_seconds, parsed.sync.reference_description) == (
        2.015,
        "Logo first appears",
    )
    assert parsed.sync.platform_offsets_seconds == {}
    assert [
        (cue.start_seconds, cue.end_seconds, cue.source_action, cue.subcategory, cue.severity)
        for cue in parsed.cues
    ] == [(3.020, 5.775, "mute", "swearing", "low"), (5.125, 7.25, "skip", "violence", "high")]


@pytest.mark.parametrize(
    ("category", "subcategory"),
    [
        ("sex", "sex"),
        ("nudity", "nudity"),
        ("violence", "violence"),
        ("violence", "gore"),
        ("language", "swearing"),
        ("discrimination", "discrimination"),
        ("drugs", "drugs"),
        ("drugs", "alcohol"),
        ("drugs", "cigarettes"),
        ("fear", "fear"),
        ("dispensable_scenes", "other"),
        ("dispensable_scenes", "tedious"),
    ],
)
def test_direct_categories_round_trip_without_a_synthetic_reference(category, subcategory):
    result = export_videoskip([_cue(category=category, subcategory=subcategory, severity="medium")])
    assert result.issues == ()
    assert result.content.startswith(b"0:00:05.125 --> ")
    parsed = parse_videoskip(result.content)
    assert parsed.sync.reference_seconds is None
    assert parsed.cues[0].subcategory == subcategory
    assert parsed.cues[0].severity == "medium"


def test_lossy_classifications_are_exported_with_preview_issues():
    result = export_videoskip(
        [
            _cue("innuendo", category="language", subcategory="innuendo", severity=None),
            _cue("sex-rape", category="sex", subcategory="sexualRape"),
            _cue("ad", category="commercial_content", subcategory="advertBreak"),
            _cue("unclassified", category=None, subcategory=None),
        ]
    )
    parsed = parse_videoskip(result.content)
    assert parsed.issues == []
    assert result.exported_count == 4
    assert [cue.subcategory for cue in parsed.cues] == ["swearing", "sex", "other", "other"]
    assert parsed.cues[0].severity == "medium"
    assert [(issue.code, issue.filter_id, issue.omitted) for issue in result.issues] == [
        ("category_mapped", "innuendo", False),
        ("severity_defaulted", "innuendo", False),
        ("category_mapped", "sex-rape", False),
        ("category_mapped", "ad", False),
        ("category_mapped", "unclassified", False),
    ]


def test_invalid_or_unsupported_cues_are_reported_without_inventing_an_action_or_range():
    result = export_videoskip(
        [
            _cue("collapsed", start_seconds=1.0001, end_seconds=1.0002),
            _cue("missing", end_seconds=None),
            _cue("too-long", end_seconds=36_000_000),
            _cue("negative", start_seconds=-1.0),
            _cue("blur").model_copy(update={"edit_action": "blur"}),
            _cue("kept", start_seconds=20.000, end_seconds=20.001, edit_action="mute"),
        ]
    )
    assert result.exported_count == 1
    assert [issue.filter_id for issue in result.issues] == [
        "collapsed",
        "missing",
        "too-long",
        "negative",
        "blur",
    ]
    assert all(issue.omitted for issue in result.issues)
    assert result.issues[-1] == VideoSkipExportIssue(
        "unsupported_action",
        "blur",
        "Only skip and mute can be exported.",
        omitted=True,
    )
    parsed = parse_videoskip(result.content)
    assert parsed.issues == []
    assert (parsed.cues[0].start_seconds, parsed.cues[0].end_seconds) == (20.000, 20.001)


def test_descriptions_cannot_inject_additional_cues_actions_or_regions():
    result = export_videoskip(
        [_cue(details="(blur) [0,0,1,1] //\r\n0:00.000 --> 0:01.000 <tag>\x00 &")],
        reference_seconds=0,
        reference_description="0:12",
    )
    parsed = parse_videoskip(result.content)
    assert parsed.issues == []
    assert len(parsed.cues) == 1
    assert parsed.cues[0].source_action == "skip"
    assert parsed.cues[0].region is None
    assert parsed.cues[0].comment == "（blur） ［0,0,1,1］ ／／ 0:00.000 → 0:01.000 ＜tag＞ ＆"
    assert parsed.sync.reference_description == "Reference: 0:12"


def test_output_respects_line_and_file_size_limits():
    cue = _cue(label="世" * (MAX_TEXT + 100))
    result = export_videoskip([cue])
    assert len(result.content.decode().splitlines()[1]) == MAX_TEXT
    assert len(parse_videoskip(result.content).cues) == 1
    with pytest.raises(ValueError, match="file size limit"):
        export_videoskip([cue] * 90)


@pytest.mark.parametrize("seconds", [-1.0, float("inf"), float("nan"), 36_000_000])
def test_invalid_reference_time_fails(seconds):
    with pytest.raises(ValueError, match="reference timestamp"):
        export_videoskip([], reference_seconds=seconds)


def test_a_description_requires_reference_timing():
    with pytest.raises(ValueError, match="requires a timestamp"):
        export_videoskip([], reference_description="Opening logo")


def test_reference_at_zero_without_a_description():
    result = export_videoskip([_cue()], reference_seconds=0)
    assert result.content.startswith(b"0:00:00.000\n\n0:00:05.125")
    parsed = parse_videoskip(result.content)
    assert parsed.issues == []
    assert parsed.sync.reference_seconds == 0
    assert parsed.sync.reference_description is None
