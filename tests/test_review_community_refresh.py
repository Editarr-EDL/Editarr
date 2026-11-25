"""Refreshing a review's community filters, community-only exports and the uniform
subtitle rule (local API §3, ADR 0002 U3–U5)."""

import pytest
from sqlalchemy import select

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.db import ReviewCommunitySourceRow, SavedReviewRow
from scanner.formats.subtitle_redaction import SubtitleMute
from scanner.models import ExportFormat
from scanner.persistence.repository.repository_review_core import get_saved_review
from scanner.persistence.repository.review_policy import prepare_review_policy
from scanner.persistence.repository.review_subtitles import subtitle_mutes
from scanner.repository import ReviewMembershipError
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import USER
from tests.test_review_community_sources import (
    FILTERS,
    activities,
    community,
    include,
    keep,
    masters,
    review,
    save,
    session,
)

__all__ = ["activities", "masters", "review"]


def _publish(masters, link, revision, *filters):
    masters.service.publish("v_1", revision, *filters)
    masters.ok("POST", f"/masters/{link['linkId']}/check", USER, {})


def test_refresh_keeps_edits_by_filter_id_and_brings_in_master_changes(masters, review):
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter("f_edit", 10_000, 20_000),
        served_filter("f_plain", 30_000, 31_000),
        served_filter("f_old", 50_000, 51_000),
        served_filter("f_gone", 70_000, 71_000),
        served_filter("f_kept", 80_000, 81_000),
        served_filter("f_deleted", 90_000, 91_000),
    )
    saved = save(masters, review, include(link["linkId"]))

    def edit(filters):
        for value in filters:
            if value["community_filter_id"] == "f_edit":
                value.update(start_seconds=11.0, severity="low", community_edited=True)
            if value["community_filter_id"] == "f_old":
                value.update(selected=False, community_edited=True)
            if value["community_filter_id"] == "f_kept":
                value.update(end_seconds=82.0, community_edited=True)
        return [value for value in filters if value["community_filter_id"] != "f_deleted"]

    edited = save(masters, review, keep(saved), edit=edit)
    masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.6})
    _publish(
        masters,
        link,
        2,
        served_filter("f_edit", 12_000, 22_000),
        served_filter("f_plain", 32_000, 33_000, severity="low"),
        served_filter("f_merged", 52_000, 53_000, mergedIds=["f_old"]),
        served_filter("f_deleted", 92_000, 93_000),
        served_filter("f_new", 100_000, 101_000, confidence=0.9),
        served_filter("f_weak", 110_000, 111_000, confidence=0.2),
    )
    with session(masters) as db:
        assert get_saved_review(db, review).community_sources[0].state == "changed"

    refreshed = save(masters, review, keep(edited, refresh=True))
    assert refreshed.revision == edited.revision + 1
    assert refreshed.community_sources[0].state == "current"
    assert refreshed.community_sources[0].master_revision == 2
    rows = community(refreshed)
    assert sorted(rows) == ["f_edit", "f_gone", "f_kept", "f_merged", "f_new", "f_plain", "f_weak"]
    # Edited: the user's values stay, under the merged ID where the filter merged.
    assert (rows["f_edit"].start_seconds, rows["f_edit"].end_seconds) == (11.0, 20.0)
    assert rows["f_edit"].severity == "low" and rows["f_edit"].community_edited
    assert rows["f_merged"].id == community(edited)["f_old"].id
    assert not rows["f_merged"].selected and rows["f_merged"].start_seconds == 50.0
    # Unedited: the new master values.
    assert (rows["f_plain"].start_seconds, rows["f_plain"].severity) == (32.0, "low")
    assert not rows["f_plain"].community_edited
    # New filters start enabled or disabled by the threshold.
    assert rows["f_new"].selected and not rows["f_weak"].selected
    # No longer served: flagged, disabled unless edited.
    assert rows["f_gone"].community.removed and not rows["f_gone"].selected
    assert rows["f_kept"].community.removed and rows["f_kept"].selected
    assert rows["f_kept"].end_seconds == 82.0
    # A filter the user deleted stays deleted.
    assert "f_deleted" not in rows


def test_a_refresh_can_follow_a_removed_filter_back(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    _publish(masters, link, 2, FILTERS[0])
    gone = save(masters, review, keep(saved, refresh=True))
    assert community(gone)["f_b"].community.removed
    _publish(masters, link, 3, *FILTERS)
    back = save(masters, review, keep(gone, refresh=True))
    assert not community(back)["f_b"].community.removed
    assert community(back)["f_b"].selected


def _community_only(masters, review_id, link_id):
    save(masters, review_id, include(link_id))
    with masters.app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review_id)
        row.memberships.clear()
        row.mcf_document.material_end_seconds = 120
        db.commit()


def _export_edl(db, review_id, stored):
    from scanner.persistence.repository.repository_review_operations import (
        export_saved_review_edl,
    )

    return export_saved_review_edl(
        db,
        review_id,
        stored.revision,
        policy_revision=stored.policy_revision,
        policy_owner_id=stored.policy_owner_id,
    )


def test_a_community_only_review_exports_edl_and_mcf_sidecars(masters, review):
    from scanner.persistence.repository.repository_review_operations import (
        export_saved_review_mcf,
    )

    link = masters.link(USER, "v_1", "2", *FILTERS)
    _community_only(masters, review, link["linkId"])
    with session(masters) as db:
        stored = get_saved_review(db, review)
        assert stored.memberships == [] and stored.community_sources
        edl = _export_edl(db, review, stored)
    assert edl.exported_scans == [] and edl.exported_format_revision == stored.edl_content_revision
    sidecar = masters.scans.file("masters").with_suffix(".edl")
    lines = [line for line in sidecar.read_text().splitlines() if not line.startswith("#")]
    assert lines == ["10.000 20.000 3", "40.000 41.000 1"]
    with session(masters) as db:
        mcf = export_saved_review_mcf(
            db,
            review,
            stored.revision,
            policy_revision=stored.policy_revision,
            policy_owner_id=stored.policy_owner_id,
        )
        summary = get_saved_review(db, review).exports.exports
    assert mcf.exported_scans == []
    assert "00:00:10.000 --> 00:00:20.000" in sidecar.with_suffix(".mcf").read_text()
    assert summary[ExportFormat.EDL].current and summary[ExportFormat.MCF].current

    # Refreshing is a new review revision, so the published sidecars become outdated.
    refreshed = save(masters, review, keep(stored, refresh=True))
    assert refreshed.exports.exports[ExportFormat.EDL].outdated is True
    assert refreshed.exports.exports[ExportFormat.MCF].outdated is True


def test_a_review_without_scans_or_community_filters_still_refuses_export(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    _community_only(masters, review, link["linkId"])
    with masters.app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review)
        row.filters.clear()
        db.commit()
    with session(masters) as db:
        stored = get_saved_review(db, review)
        with pytest.raises(ReviewMembershipError, match="at least one scan or community filter"):
            _export_edl(db, review, stored)


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_a_community_only_publication_regenerates_from_its_retained_filters(
    masters, review, format
):
    import json

    from scanner.db import MediaItemExportRow
    from scanner.persistence.repository.export_publication import publish_export
    from scanner.persistence.repository.export_recovery import publication_lock
    from scanner.persistence.repository.export_retained import prepare_retained_export
    from scanner.security.access.vocabulary import Role
    from tests.multi_user_access_support import build_access

    link = masters.link(USER, "v_1", "2", *FILTERS)
    _community_only(masters, review, link["linkId"])
    with session(masters) as db, publication_lock(db):
        stored = get_saved_review(db, review)
        result = publish_export(
            db, review, stored.revision, ExportFormat(format), access=build_access(Role.USER)
        )
    assert result is not None
    _publish(masters, link, 2, served_filter("f_z", 5_000, 6_000))
    with masters.app.accounts.factory() as db:
        row = db.scalars(select(MediaItemExportRow)).one()
        snapshot = json.loads(row.snapshot_json)
        assert snapshot["sources"] == []
        assert prepare_retained_export(db, row, snapshot) == row.content_bytes
    assert b"10.000" in row.content_bytes or b"00:00:10.000" in row.content_bytes


def test_retained_snapshots_from_before_review_filters_keep_their_community_filters():
    from scanner.persistence.repository.export_retained import legacy_community_filters

    legacy = {
        "base_filters": [],
        "community_sources": [
            {
                "source_id": "s1",
                "filters": [
                    {
                        "filter_id": "f_a",
                        "category": "violence",
                        "subcategory": "punching",
                        "severity": "high",
                        "edit_action": "mute",
                        "start_seconds": 1.0,
                        "end_seconds": 2.0,
                    },
                    {
                        "filter_id": "f_bad",
                        "category": "violence",
                        "subcategory": "swearing",
                        "severity": "high",
                        "edit_action": "skip",
                        "start_seconds": 3.0,
                        "end_seconds": 4.0,
                    },
                ],
            }
        ],
    }
    (value,) = legacy_community_filters(legacy)
    assert (value.community_filter_id, value.source, value.mcf_channel, value.selected) == (
        "f_a",
        "audio",
        "audio",
        True,
    )
    current = {**legacy, "base_filters": [{"community_source_id": "s1"}]}
    assert legacy_community_filters(current) == ()


def test_subtitles_redact_enabled_audio_language_mutes_with_words_whatever_their_source(
    masters, review
):
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter(
            "f_word", 10_000, 12_000, action="mute", category="language", subcategory="swearing"
        ),
        served_filter(
            "f_named", 20_000, 22_000, action="mute", category="language", subcategory="swearing"
        ),
        served_filter(
            "f_off", 30_000, 32_000, action="mute", category="language", subcategory="swearing"
        ),
    )
    saved = save(masters, review, include(link["linkId"]))

    def edit(filters):
        for value in filters:
            if value["community_filter_id"] == "f_named":
                value["label"] = "darn"
            if value["community_filter_id"] == "f_off":
                value.update(label="heck", selected=False, community_edited=True)

    save(masters, review, keep(saved), edit=edit)
    with session(masters) as db:
        policy = prepare_review_policy(db, db.get(SavedReviewRow, review))
        assert subtitle_mutes(policy, 120) == (SubtitleMute(20.0, 22.0, "darn"),)
        assert "community_subtitles" not in get_saved_review(db, review).model_dump()
        assert db.scalars(select(ReviewCommunitySourceRow)).one()
