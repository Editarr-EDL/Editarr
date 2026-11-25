"""Sharing projection: local cues and community review filter edits as a v3 submission
(D21–D22, ADR 0002 U6)."""

import json

import pytest

from scanner.formats.community.artifact_v3 import encode_artifact_v3
from scanner.integrations.community.submission_projection import (
    LinkProjection,
    SubmissionOptions,
    project_submission,
)
from scanner.integrations.community.submissions import version_choice
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_filter_proposals import LinkedFilter, inverse_alignment
from scanner.modeling.community_match_keys import HashKey
from scanner.modeling.community_normalization import SEEDED_ALIASES
from scanner.modeling.community_work_identity import WorkIdentityRequired, work_identity
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier

MOVIE = MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "603", "jellyfin.item"),))
KEY = HashKey("md5_quick", "a" * 32, 4_823_749_120)


def finding(start, end, category="violence", subcategory="punching", **extra):
    return {
        "id": extra.pop("id", int(start)),
        "scan_id": "scan",
        "category": category,
        "subcategory": subcategory,
        "severity": "medium",
        "start_seconds": start,
        "end_seconds": end,
        "source": "video",
        "selected": True,
        "edit_action": "skip",
        "manually_added": None,
        "manually_edited": None,
        **extra,
    }


def scan_data(*findings, stages=()):
    scan = {
        "id": "scan",
        "origin": "generated",
        "release_version": "1.4.0",
        "duration_seconds": 3600.0,
        "resolved_local_path": "/m.mkv",
        "scan_video": True,
    }
    return {
        "root": scan,
        "scans": [scan],
        "findings": list(findings),
        "base_findings": list(findings),
        "policy_evidence": [],
        "stages": list(stages),
        "synchronization": [],
    }


def row(filter_id, start, end, category="violence", **values):
    defaults = {"subcategory": "punching", "severity": "high", "action": "skip"}
    defaults.update(values)
    return LinkedFilter(
        filter_id,
        category,
        start,
        end,
        defaults["subcategory"],
        defaults["severity"],
        defaults["action"],
    )


def options(**changes):
    values = dict(
        submission_id="sub_1",
        contributor_id="c_7f3a",
        key_id="sha256:" + "b" * 64,
        created_at=1795553648,
        work=work_identity(MOVIE, title="The Matrix", year=1999),
        version_choice={"choice": "alias", "aliasId": "theatrical"},
        duration_ms=3_600_000,
        match_keys=(KEY,),
    )
    return SubmissionOptions(**{**values, **changes})


def entries(projection):
    return {entry.entry_id: entry for entry in projection.artifact.entries}


def test_cues_support_the_master_filters_they_overlap():
    link = LinkProjection(
        "v_1",
        4,
        {"offsetMs": 1000, "scale": 1.0, "anchors": 3},
        (row("f_kept", 100_200, 101_100), row("f_untouched", 500_000, 501_000)),
    )
    data = scan_data(
        finding(100.0, 101.0),
        finding(200.5, 201.0),
        finding(900.0, 901.0, category="language", subcategory="swearing"),
        finding(950.0, 951.0, selected=False),
        finding(960.0, None),
    )
    projection = project_submission(data, "scan", options(), link)
    by_id = entries(projection)
    assert [entry.proposed_filter_id for entry in by_id.values()] == ["f_kept", None, None]
    assert by_id["e1"].evidence.origin == "model" and by_id["e1"].evidence.model_version == "1.4.0"
    assert projection.estimates.as_json() == {
        "approximate": True,
        "newFilters": 2,
        "confirmations": 1,
        "adjustments": 0,
        "disputes": 0,
    }
    assert projection.artifact.alignment_hint.model_dump(by_alias=True) == {
        "versionId": "v_1",
        "revision": 4,
        "offsetMs": -1000,
        "scale": 1.0,
        "anchors": 3,
    }
    assert "unclassified_cue_skipped" in projection.warnings
    encode_artifact_v3(projection.artifact)


def _entry(filter_id, start_ms, end_ms, *, default=True, **values):
    return {
        "filter_id": filter_id,
        "category": "violence",
        "subcategory": "punching",
        "severity": "high",
        "edit_action": "skip",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": 0.5,
        "default_selected": default,
        **values,
    }


def _community(filter_id, start, end, selected=True, edited=True, source="src", **values):
    return {
        **finding(start, end, **{"severity": "high", **values}),
        "id": f"r-{filter_id}",
        "selected": selected,
        "source_scan_id": None,
        "source_finding_id": None,
        "mcf_channel": "both",
        "community_source_id": source,
        "community_filter_id": filter_id,
        "community_edited": edited,
    }


def _review(sources, rows):
    data = scan_data()
    data["root"] = {"id": "review"}
    data["community_sources"] = [
        {"id": source_id, "community_version_id": version, "filters_json": json.dumps(entries)}
        for source_id, version, entries in sources
    ]
    data["findings"] = data["base_findings"] = rows
    data["policy_evidence"] = [finding(200.0, 201.5)]
    return data


def _shared(projection):
    return [
        (entry.kind, entry.proposed_filter_id, entry.start_ms, entry.severity)
        for entry in projection.artifact.entries
    ]


def test_review_community_filters_share_only_the_users_edits():
    entries = [
        _entry("f_same", 100_000, 101_000),
        _entry("f_moved", 200_000, 201_000),
        _entry("f_off", 300_000, 301_000),
        _entry("f_low", 400_000, 401_000, default=False),
        _entry("f_on", 500_000, 501_000, default=False),
        _entry("f_bulk", 600_000, 601_000),
        _entry("f_gone", 700_000, 701_000, served=False),
    ]
    rows = [
        _community("f_same", 100.0, 101.0, edited=False),
        _community("f_moved", 200.5, 201.0, severity="low"),
        _community("f_off", 300.0, 301.0, selected=False),
        # Started disabled, stayed disabled: not sent, even with adjusted bounds.
        _community("f_low", 400.5, 401.0, selected=False),
        _community("f_on", 500.0, 501.0),
        # Changed only by bulk defaults: not the user's edit.
        _community("f_bulk", 600.0, 601.0, selected=False, edited=False),
        _community("f_gone", 700.0, 701.0, selected=False),
        {**finding(300.0, 301.0), "source_scan_id": "scan", "source_finding_id": 3},
    ]
    data = _review([("src", "v_1", entries)], rows)
    link = LinkProjection("v_1", 1, None, (row("f_off", 300_000, 301_000),))
    projection = project_submission(data, "review", options(), link)
    assert _shared(projection) == [
        ("support", None, 300_000, "medium"),
        ("support", "f_moved", 200_500, "low"),
        ("dispute", "f_off", 300_000, None),
        ("support", "f_on", 500_000, "high"),
    ]
    moved = projection.artifact.entries[1].evidence
    assert (moved.origin, moved.manual_review, moved.locally_verified) == (
        "downloaded",
        True,
        True,
    )
    assert projection.estimates.as_json() == {
        "approximate": True,
        "newFilters": 1,
        "confirmations": 2,
        "adjustments": 1,
        "disputes": 1,
    }
    encode_artifact_v3(projection.artifact)


def test_only_snapshots_of_the_submissions_version_are_attested():
    rows = [_community("f_a", 100.0, 101.0, severity="low", source="old")]
    data = _review([("old", "v_old", [_entry("f_a", 100_000, 101_000)])], rows)
    link = LinkProjection("v_1", 1, None, ())
    assert _shared(project_submission(data, "review", options(), link)) == []
    assert _shared(project_submission(data, "review", options(), None)) == []
    same = LinkProjection("v_old", 1, None, ())
    assert _shared(project_submission(data, "review", options(), same)) == [
        ("support", "f_a", 100_000, "low")
    ]


def _stage(stage, status="complete", **settings):
    return {
        "stage": stage,
        "status": status,
        "effective_settings_json": json.dumps(settings),
        "intervals_json": json.dumps([{"start_seconds": 0.0, "end_seconds": 3600.0}]),
    }


def _coverage(*stages):
    data = scan_data(finding(10.0, 12.0), stages=stages)
    projection = project_submission(data, "scan", options(), None)
    return {item.category: item.intervals for item in projection.artifact.coverage}, projection


def test_without_a_link_every_cue_is_new_and_coverage_comes_from_completed_stages():
    coverage, projection = _coverage(
        _stage("audio_scan", audio_filter="better_profanity"),
        _stage("visual", status="partial", video_model="nudenet"),
    )
    assert projection.artifact.alignment_hint is None
    assert [entry.proposed_filter_id for entry in projection.artifact.entries] == [None]
    assert list(coverage) == ["language"]
    assert coverage["language"][0].model_dump(by_alias=True) == {"startMs": 0, "endMs": 3_600_000}
    assert projection.estimates.new_filters == 1


# commercial_content and dispensable_scenes are never claimed: no automated stage detects them.
VIDEO_CATEGORIES = [
    "discrimination",
    "drugs",
    "fear",
    "nudity",
    "sex",
    "violence",
]


@pytest.mark.parametrize("model", ["nudenet", "shieldstral", "qwen3-vl-8b", None])
def test_visual_coverage_claims_every_detectable_video_category_regardless_of_model(model):
    settings = {} if model is None else {"video_model": model}
    coverage, _projection = _coverage(_stage("visual", **settings))
    assert sorted(coverage) == VIDEO_CATEGORIES


@pytest.mark.parametrize("audio_filter", ["better_profanity", "qwen", None])
def test_audio_only_scan_covers_only_audio_categories(audio_filter):
    settings = {} if audio_filter is None else {"audio_filter": audio_filter}
    coverage, _projection = _coverage(
        _stage("audio_scan", **settings), _stage("audio_extract"), _stage("setup")
    )
    assert list(coverage) == ["language"]


def test_audio_and_visual_scan_covers_every_category():
    coverage, _projection = _coverage(_stage("visual"), _stage("audio_scan"))
    assert sorted(coverage) == sorted([*VIDEO_CATEGORIES, "language"])


def test_reviews_mark_manual_cues_and_downloaded_cues_need_local_verification():
    imported = {
        "id": "old",
        "origin": "imported",
        "release_version": None,
        "duration_seconds": 3600.0,
    }
    data = scan_data()
    data["root"] = {"id": "review"}
    data["scans"].append(imported)
    data["synchronization"] = [{"start_seconds": 0.0, "end_seconds": 3600.0}]
    data["findings"] = [
        {**finding(10.0, 11.0), "source_scan_id": "scan", "source_finding_id": None},
        {**finding(20.0, 21.0), "source_scan_id": "old", "source_finding_id": 7},
        {**finding(30.0, 31.0), "source_scan_id": "old", "source_finding_id": 8},
    ]
    data["policy_evidence"] = [finding(30.5, 31.5)]
    projection = project_submission(data, "review", options(), None)
    evidence = [entry.evidence for entry in projection.artifact.entries]
    assert [(item.origin, item.manual_review, item.locally_verified) for item in evidence] == [
        ("manual", True, True),
        ("downloaded", True, False),
        ("downloaded", True, True),
    ]
    assert projection.artifact.coverage == []


def test_version_choice_and_work_identity_are_required():
    assert version_choice({"choice": "alias", "alias_id": "extended"}, SEEDED_ALIASES)[1] == {
        "choice": "alias",
        "aliasId": "extended",
        "label": "Extended Edition",
    }
    assert version_choice({"choice": "other", "text": " "}, SEEDED_ALIASES)[0] == {
        "choice": "other",
        "text": None,
    }
    assert version_choice({"choice": "other", "text": "IMAX"}, SEEDED_ALIASES)[0]["text"] == "IMAX"
    for choice, code in (
        (None, "version_choice_required"),
        ({"choice": "alias", "alias_id": "imax"}, "unknown_alias"),
        ({"choice": "other", "text": "x" * 65}, "version_choice_required"),
    ):
        with pytest.raises(CommunityFailure, match=code):
            version_choice(choice, SEEDED_ALIASES)
    with pytest.raises(WorkIdentityRequired):
        work_identity(MediaIdentity("movie", ()), title="The Matrix", year=1999)


def test_inverse_alignment_maps_local_back_to_the_master_timeline():
    assert inverse_alignment(None) == (0, 1.0)
    offset, scale = inverse_alignment({"offsetMs": 1200, "scale": 1.04})
    assert round(1000 * 1.04 + 1200) * scale + offset == pytest.approx(1000, abs=1)
    assert inverse_alignment({"offsetMs": 0, "scale": 1.3}) is None
