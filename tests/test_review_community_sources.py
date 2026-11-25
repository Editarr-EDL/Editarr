"""Saved reviews include community filters as stored snapshots of a master link."""

from contextlib import contextmanager

import pytest
from sqlalchemy import select

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.db import (
    ReviewCommunitySourceRow,
    ReviewMcfDocumentRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    UserRow,
)
from scanner.models import (
    ExportFormat,
    ReviewCommunityMembership,
    ReviewDuplicate,
    ReviewSave,
)
from scanner.persistence.repository.account_community_masters import (
    delete_master_state,
    transfer_master_state,
)
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.repository_review_core import get_saved_review
from scanner.persistence.repository.repository_review_mutation import (
    duplicate_saved_review,
    save_saved_review,
)
from scanner.persistence.repository.review_community_sources import review_filters
from scanner.persistence.repository.review_export_content import render_review_export
from scanner.persistence.repository.review_policy import prepare_review_policy
from scanner.repository import ReviewMembershipError
from tests.community_master_http_support import activities, masters
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import ADMIN, PEER_USER, USER

__all__ = ["activities", "masters"]

FILTERS = (
    served_filter("f_a", 10_000, 20_000),
    served_filter("f_b", 40_000, 41_000, action="mute", subcategory="punching"),
)


@pytest.fixture
def review(masters):
    """USER's saved review of the seeded version, with its scan as evidence."""
    review_id = masters.scans.seed_review(USER, "community", masters.scan_id)
    with masters.app.accounts.factory() as db:
        db.get(SavedReviewRow, review_id).memberships[0].role = "video"
        db.add(ReviewMcfDocumentRow(review_id=review_id, material_start_seconds=0))
        db.add(
            ReviewSynchronizationRow(
                review_id=review_id, start_seconds=0, end_seconds=120, end_mode="file_end"
            )
        )
        db.commit()
    return review_id


@contextmanager
def session(masters, actor=USER):
    with masters.app.accounts.factory() as db:
        revision = db.scalar(select(UserRow.permission_revision).where(UserRow.id == actor))
        db.info["review_actor"] = ActivityOwner(actor, revision)
        yield db


def save(masters, review_id, memberships, actor=USER):
    with session(masters, actor) as db:
        stored = get_saved_review(db, review_id)
        return save_saved_review(
            db,
            review_id,
            ReviewSave(
                revision=stored.revision,
                name=stored.name,
                memberships=stored.memberships,
                filters=[value.model_dump(exclude={"label_edited"}) for value in stored.filters],
                mcf_document=stored.mcf_document,
                synchronization=stored.synchronization,
                community_memberships=memberships,
            ),
        )


def include(link_id):
    return [ReviewCommunityMembership(link_id=link_id)]


def keep(saved, refresh=False):
    return [
        ReviewCommunityMembership(source_id=value.source_id, refresh=refresh)
        for value in saved.community_sources
    ]


def spans(source):
    return [(item.filter_id, item.start_seconds, item.end_seconds) for item in source.filters]


def test_including_stores_a_snapshot_that_later_changes_only_flag(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    (source,) = saved.community_sources
    assert source.state == "current" and source.link_id == link["linkId"]
    assert source.master_revision == 1 and source.label == "Theatrical"
    assert spans(source) == [("f_a", 10.0, 20.0), ("f_b", 40.0, 41.0)]
    assert source.filters[0].confidence == 0.5
    assert (saved.revision, saved.edl_content_revision) == (1, 1)

    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_b", USER, {"decision": "reject"})
    masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.9})
    with session(masters) as db:
        later = get_saved_review(db, review)
    assert later.community_sources[0].state == "changed"
    assert spans(later.community_sources[0]) == spans(source)
    assert (later.revision, later.content_revision, later.edl_content_revision) == (1, 1, 1)


def test_refresh_and_removal_advance_the_review_revisions(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_b", USER, {"decision": "reject"})
    unchanged = save(masters, review, keep(saved))
    assert unchanged.revision == saved.revision

    refreshed = save(masters, review, keep(saved, refresh=True))
    (source,) = refreshed.community_sources
    assert source.source_id == saved.community_sources[0].source_id
    assert source.state == "current" and spans(source) == [("f_a", 10.0, 20.0)]
    assert refreshed.revision == saved.revision + 1
    assert refreshed.edl_content_revision == saved.edl_content_revision + 1
    assert refreshed.mcf_content_revision == saved.mcf_content_revision + 1

    removed = save(masters, review, [])
    assert removed.community_sources == [] and removed.revision == refreshed.revision + 1
    with masters.app.accounts.factory() as db:
        assert db.scalars(select(ReviewCommunitySourceRow)).all() == []


def test_exports_read_only_the_snapshot(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    save(masters, review, include(link["linkId"]))
    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_a", USER, {"decision": "reject"})
    with session(masters) as db:
        row = db.get(SavedReviewRow, review)
        policy = prepare_review_policy(db, row)
        filters = review_filters(row)
        assert [item.filter_id for item in filters] == ["f_a", "f_b"]
        edl, _ = render_review_export(row, policy, ExportFormat.EDL, 120, filters)
        row.mcf_document.material_start_seconds, row.mcf_document.material_end_seconds = 0, 120
        mcf, _ = render_review_export(row, policy, ExportFormat.MCF, 120, filters)
    lines = [line for line in edl.decode().splitlines() if not line.startswith("#")]
    assert lines == ["10.000 20.000 3", "40.000 41.000 1"]
    text = mcf.decode()
    assert "00:00:10.000 --> 00:00:20.000" in text and "00:00:40.000 --> 00:00:41.000" in text


def test_only_the_owner_may_include_their_own_aligned_link(masters, review):
    peer = masters.link(PEER_USER, "v_1", "2", *FILTERS)
    with pytest.raises(ReviewMembershipError, match="owner's master set"):
        save(masters, review, include(peer["linkId"]))
    fuzzy = masters.link(USER, "v_2", "4", *FILTERS)
    with pytest.raises(ReviewMembershipError, match="Align"):
        save(masters, review, include(fuzzy["linkId"]))
    own = masters.link(ADMIN, "v_1", "2", *FILTERS)
    with pytest.raises(ReviewMembershipError, match="owner's master set"):
        save(masters, review, include(own["linkId"]), actor=ADMIN)
    with pytest.raises(ValueError, match="exactly one"):
        ReviewCommunityMembership(link_id="a", source_id="b")


def test_duplicates_copy_the_snapshot(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    for actor, link_id in ((USER, link["linkId"]), (ADMIN, None)):
        with session(masters, actor) as db:
            copy = duplicate_saved_review(
                db,
                review,
                ReviewDuplicate(
                    revision=saved.revision,
                    name=f"copy {actor}",
                    policy_revision=saved.policy_revision,
                    policy_owner_id=saved.policy_owner_id,
                ),
            )
        (source,) = copy.community_sources
        assert source.link_id == link_id and spans(source) == spans(saved.community_sources[0])
        assert source.source_id != saved.community_sources[0].source_id
        assert source.state == ("current" if link_id else "link_unavailable")


def test_account_deletion_keeps_snapshots_and_transfer_and_replace_repoint(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    save(masters, review, include(link["linkId"]))
    masters.service.publish("v_2", 1, served_filter("f_z"))
    replaced = masters.apply(USER, "v_2", replace=True).json()["link"]
    with session(masters) as db:
        (source,) = get_saved_review(db, review).community_sources
    assert source.link_id == replaced["linkId"] and source.state == "changed"
    assert [item.filter_id for item in source.filters] == ["f_a", "f_b"]

    peer = masters.link(PEER_USER, "v_1", "2", *FILTERS)
    with masters.app.accounts.factory() as db:
        transfer_master_state(db, USER, PEER_USER)
        db.commit()
    with masters.app.accounts.factory() as db:
        assert db.scalar(select(ReviewCommunitySourceRow.link_id)) == peer["linkId"]
        delete_master_state(db, PEER_USER)
        db.commit()
    with session(masters) as db:
        (source,) = get_saved_review(db, review).community_sources
    assert source.link_id is None and source.state == "link_unavailable"
    assert [item.filter_id for item in source.filters] == ["f_a", "f_b"]


def test_hide_rejected_downvoted_is_a_saved_per_account_preference(masters):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = masters.ok("PATCH", "/preferences", USER, {"hide_rejected_downvoted": True})
    assert saved == {
        "version": 1,
        "confidenceThreshold": 0.0,
        "showLowConfidence": False,
        "hideRejectedDownvoted": True,
    }
    assert masters.ok("GET", "/preferences", PEER_USER)["hideRejectedDownvoted"] is False
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert view["preferences"]["hideRejectedDownvoted"] is True
    assert all(row["active"] for row in view["filters"])
    refused = masters.send("PATCH", "/preferences", USER, {"hide_rejected_downvoted": "yes"})
    assert refused.status_code == 422


def test_snapshot_changes_need_evidence_edit_and_community_search(monkeypatch):
    from types import SimpleNamespace

    from scanner.persistence.repository import review_access
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.decisions import AccessDenied

    required = []
    monkeypatch.setattr(
        review_access, "require", lambda _access, action, _target: required.append(action)
    )
    stored = SimpleNamespace(community_sources=[SimpleNamespace(source_id="s1")])

    def check(memberships, capabilities):
        caller = SimpleNamespace(access=SimpleNamespace(capabilities=capabilities))
        draft = SimpleNamespace(community_memberships=memberships)
        review_access.ReviewAccess._require_community_changes(caller, stored, draft, "target")

    check(None, set())
    check([ReviewCommunityMembership(source_id="s1")], set())
    assert required == []
    check([], set())
    assert len(required) == 1
    with pytest.raises(AccessDenied):
        check([ReviewCommunityMembership(source_id="s1", refresh=True)], set())
    check([ReviewCommunityMembership(link_id="l1")], {C.COMMUNITY_SEARCH})
    assert len(required) == 3


def _community_only(masters, review_id, link_id):
    save(masters, review_id, include(link_id))
    with masters.app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review_id)
        row.memberships.clear()
        row.mcf_document.material_end_seconds = 120
        db.commit()


def test_a_community_only_review_exports_edl_and_mcf_sidecars(masters, review):
    from scanner.persistence.repository.repository_review_operations import (
        export_saved_review_edl,
        export_saved_review_mcf,
    )

    link = masters.link(USER, "v_1", "2", *FILTERS)
    _community_only(masters, review, link["linkId"])
    with session(masters) as db:
        stored = get_saved_review(db, review)
        assert stored.memberships == [] and stored.community_sources
        edl = export_saved_review_edl(
            db,
            review,
            stored.revision,
            policy_revision=stored.policy_revision,
            policy_owner_id=stored.policy_owner_id,
        )
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
    from scanner.persistence.repository.repository_review_operations import (
        export_saved_review_edl,
    )

    link = masters.link(USER, "v_1", "2", *FILTERS)
    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_a", USER, {"decision": "reject"})
    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_b", USER, {"decision": "reject"})
    _community_only(masters, review, link["linkId"])
    with session(masters) as db:
        stored = get_saved_review(db, review)
        assert stored.community_sources[0].filters == []
        with pytest.raises(ReviewMembershipError, match="at least one scan or community filter"):
            export_saved_review_edl(
                db,
                review,
                stored.revision,
                policy_revision=stored.policy_revision,
                policy_owner_id=stored.policy_owner_id,
            )


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_a_community_only_publication_retains_its_snapshot_for_policy_regeneration(
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
    masters.ok("PATCH", f"/masters/{link['linkId']}/filters/f_a", USER, {"decision": "reject"})
    with masters.app.accounts.factory() as db:
        row = db.scalars(select(MediaItemExportRow)).one()
        snapshot = json.loads(row.snapshot_json)
        assert snapshot["sources"] == []
        assert prepare_retained_export(db, row, snapshot) == row.content_bytes
    assert b"10.000" in row.content_bytes or b"00:00:10.000" in row.content_bytes


def test_snapshots_are_unclipped_so_a_later_runtime_is_not_a_change(masters, review):
    link = masters.link(USER, "v_1", "2", served_filter("f_tail", 95_000, 130_000))
    saved = save(masters, review, include(link["linkId"]))
    masters.set_duration(100)
    with session(masters) as db:
        (source,) = get_saved_review(db, review).community_sources
        row = db.get(SavedReviewRow, review)
        edl, _ = render_review_export(
            row, prepare_review_policy(db, row), ExportFormat.EDL, 100, review_filters(row)
        )
    assert source.state == "current" and spans(source) == spans(saved.community_sources[0])
    assert spans(source) == [("f_tail", 95.0, 130.0)]
    assert [line for line in edl.decode().splitlines() if not line.startswith("#")] == [
        "95.000 100.000 3"
    ]


def test_community_only_export_needs_a_usable_filter_and_clips_to_the_probed_file(
    masters, review, monkeypatch
):
    from scanner import repository as root
    from scanner.persistence.repository.repository_review_operations import (
        export_saved_review_edl,
    )

    def export(db, stored):
        return export_saved_review_edl(
            db,
            review,
            stored.revision,
            policy_revision=stored.policy_revision,
            policy_owner_id=stored.policy_owner_id,
        )

    link = masters.link(USER, "v_1", "2", served_filter("f_wrong", 10_000, 20_000))
    _community_only(masters, review, link["linkId"])
    # The service refuses misclassified filters, so only a stored snapshot can hold one.
    with masters.app.accounts.factory() as db:
        row = db.scalars(select(ReviewCommunitySourceRow)).one()
        row.filters_json = row.filters_json.replace('"punching"', '"swearing"')
        db.commit()
    with session(masters) as db:
        stored = get_saved_review(db, review)
        assert stored.community_sources[0].unusable_filter_ids == ["f_wrong"]
        with pytest.raises(ReviewMembershipError, match="at least one scan or community filter"):
            export(db, stored)

    masters.service.publish("v_2", 1, served_filter("f_tail", 95_000, 130_000))
    replaced = masters.apply(USER, "v_2", replace=True).json()["link"]
    save(masters, review, [])
    save(masters, review, include(replaced["linkId"]))
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 100.0)
    with session(masters) as db:
        export(db, get_saved_review(db, review))
    sidecar = masters.scans.file("masters").with_suffix(".edl")
    lines = [line for line in sidecar.read_text().splitlines() if not line.startswith("#")]
    assert lines == ["95.000 100.000 3"]


SWEAR = served_filter(
    "f_swear", 39_000, 42_000, category="language", subcategory="swearing", action="mute"
)


def _audio_word(masters, start, end, word):
    """Make the seeded scan a completed audio scan with one word-level language finding."""
    from scanner.db import ScanFindingRow, ScanRow

    with masters.app.accounts.factory() as db:
        db.get(ScanRow, masters.scan_id).scan_audio = True
        db.add(
            ScanFindingRow(
                scan_id=masters.scan_id,
                category="language",
                subcategory="swearing",
                start_seconds=start,
                end_seconds=end,
                label=word,
                source="audio",
                edit_action="mute",
            )
        )
        db.commit()


def _subtitle_mutes(masters, review):
    from scanner.persistence.repository.review_community_subtitles import (
        community_subtitle_mutes,
    )

    with session(masters) as db:
        result = community_subtitle_mutes(db, db.get(SavedReviewRow, review), None)
        detail = get_saved_review(db, review).community_subtitles
    assert detail == result.summary
    return result


@pytest.mark.parametrize(
    ("word", "applied"), [((40.0, 40.5), True), ((80.0, 80.5), False), (None, False)]
)
def test_community_mutes_reach_subtitles_only_through_an_aligned_local_word(
    masters, review, word, applied
):
    from scanner.formats.subtitle_redaction import redact_subtitles
    from scanner.formats.subtitles import parse_subtitles

    link = masters.link(USER, "v_1", "2", SWEAR)
    save(masters, review, include(link["linkId"]))
    if word is not None:
        _audio_word(masters, *word, "damn")
    result = _subtitle_mutes(masters, review)
    summary = result.summary.model_dump()
    expected = {"applied": 0, "needs_audio_scan": 0, "unaligned": 0}
    expected["applied" if applied else "unaligned" if word else "needs_audio_scan"] = 1
    assert summary == expected
    content = b"1\r\n00:00:39,000 --> 00:00:42,000\r\nWell damn it.\r\n"
    redaction = redact_subtitles(parse_subtitles(content, "srt"), result.mutes)
    rendered = redaction.document.render(redaction.replacements)
    assert (b"damn" not in rendered) is applied
    if applied:
        assert result.mutes[0].expression == "damn"
