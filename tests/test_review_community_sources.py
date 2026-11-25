"""Saved reviews include community filters as normal review filters from a stored snapshot
of a master link (local API §3, ADR 0002 U2–U4)."""

from contextlib import contextmanager

import pytest
from sqlalchemy import select

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.db import (
    ReviewCommunitySourceRow,
    ReviewFilterRow,
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
from scanner.persistence.repository.review_export_content import render_review_export
from scanner.persistence.repository.review_policy import prepare_review_policy
from scanner.repository import ReviewMembershipError
from tests.community_master_http_support import activities, masters
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import ADMIN, PEER_USER, USER

__all__ = ["activities", "masters", "review", "save", "include", "keep", "community", "session"]

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


def save(masters, review_id, memberships=None, actor=USER, edit=None):
    """Save the stored review again; `edit(filters)` may change the filter dicts first."""
    with session(masters, actor) as db:
        stored = get_saved_review(db, review_id)
        filters = [
            value.model_dump(exclude={"label_edited", "community"}) for value in stored.filters
        ]
        if edit is not None:
            filters = edit(filters) or filters
        return save_saved_review(
            db,
            review_id,
            ReviewSave(
                revision=stored.revision,
                name=stored.name,
                memberships=stored.memberships,
                filters=filters,
                mcf_document=stored.mcf_document,
                synchronization=stored.synchronization,
                community_memberships=memberships,
                policy_owner_id=stored.policy_owner_id,
                policy_revision=stored.policy_revision,
            ),
        )


def include(link_id):
    return [ReviewCommunityMembership(link_id=link_id)]


def keep(saved, refresh=False):
    return [
        ReviewCommunityMembership(source_id=value.source_id, refresh=refresh)
        for value in saved.community_sources
    ]


def community(review):
    """The review's community filters by master filter ID."""
    return {value.community_filter_id: value for value in review.filters if value.community}


def spans(review):
    return sorted(
        (fid, value.start_seconds, value.end_seconds, value.selected)
        for fid, value in community(review).items()
    )


def test_included_filters_are_normal_review_filters_with_community_provenance(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    (source,) = saved.community_sources
    assert source.state == "current" and source.link_id == link["linkId"]
    assert source.master_revision == 1 and source.label == "Theatrical"
    assert source.filter_count == 2
    assert spans(saved) == [("f_a", 10.0, 20.0, True), ("f_b", 40.0, 41.0, True)]
    skip, mute = community(saved)["f_a"], community(saved)["f_b"]
    assert (skip.source, skip.mcf_channel, skip.edit_action) == ("visual", "both", "skip")
    assert (mute.source, mute.mcf_channel, mute.edit_action) == ("audio", "audio", "mute")
    assert skip.source_scan_id is None and skip.community_source_id == source.source_id
    assert skip.label is None and skip.confidence == 0.5
    assert skip.community.label == "Theatrical" and skip.community.contributor_count == 2
    assert not skip.community_edited and not skip.community.removed
    assert (saved.revision, saved.edl_content_revision) == (1, 1)

    masters.service.publish("v_1", 2, served_filter("f_a", 11_000, 20_000))
    masters.ok("POST", f"/masters/{link['linkId']}/check", USER, {})
    with session(masters) as db:
        later = get_saved_review(db, review)
    assert later.community_sources[0].state == "changed"
    assert spans(later) == spans(saved)
    assert (later.revision, later.content_revision, later.edl_content_revision) == (1, 1, 1)


def test_threshold_and_low_confidence_decide_which_filters_start_enabled(masters, review):
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter("f_strong", 10_000, 20_000, confidence=0.8),
        served_filter("f_weak", 30_000, 31_000, confidence=0.2),
        served_filter("f_low", 50_000, 51_000, confidence=0.9, lowConfidence=True),
    )
    masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.5})
    saved = save(masters, review, include(link["linkId"]))
    assert {fid: value.selected for fid, value in community(saved).items()} == {
        "f_strong": True,
        "f_weak": False,
        "f_low": False,
    }
    assert community(saved)["f_low"].community.low_confidence
    masters.ok("PATCH", "/preferences", USER, {"show_low_confidence": True})
    save(masters, review, [])
    again = save(masters, review, include(link["linkId"]))
    assert community(again)["f_low"].selected


def test_enabling_disabling_and_adjusting_work_like_scan_filters(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))

    def edit(filters):
        for value in filters:
            if value["community_filter_id"] == "f_a":
                value.update(
                    start_seconds=11.0, severity="low", subcategory="kicking", community_edited=True
                )
            if value["community_filter_id"] == "f_b":
                value.update(selected=False, community_edited=True)

    edited = save(masters, review, keep(saved), edit=edit)
    assert edited.revision == saved.revision + 1
    assert edited.edl_content_revision == saved.edl_content_revision + 1
    adjusted, disabled = community(edited)["f_a"], community(edited)["f_b"]
    assert (adjusted.start_seconds, adjusted.severity, adjusted.subcategory) == (
        11.0,
        "low",
        "kicking",
    )
    assert adjusted.community_edited and disabled.community_edited and not disabled.selected
    with session(masters) as db:
        row = db.get(SavedReviewRow, review)
        policy = prepare_review_policy(db, row)
        edl, _ = render_review_export(row, policy, ExportFormat.EDL, 120)
        row.mcf_document.material_start_seconds, row.mcf_document.material_end_seconds = 0, 120
        mcf, _ = render_review_export(row, policy, ExportFormat.MCF, 120)
    assert [line for line in edl.decode().splitlines() if not line.startswith("#")] == [
        "11.000 20.000 3"
    ]
    text = mcf.decode()
    assert "00:00:11.000 --> 00:00:20.000" in text and "kicking" in text
    assert "00:00:40.000" not in text


def test_community_filter_provenance_is_checked(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))

    def unknown(filters):
        for value in filters:
            if value["community_filter_id"] == "f_a":
                value["community_filter_id"] = "f_unknown"

    with pytest.raises(ReviewMembershipError, match="provenance"):
        save(masters, review, keep(saved), edit=unknown)

    def dropped(filters):
        for value in filters:
            value["community_source_id"] = value["community_source_id"] and "other"

    with pytest.raises(ReviewMembershipError, match="community source"):
        save(masters, review, keep(saved), edit=dropped)


def test_removing_the_source_removes_its_filters(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    unchanged = save(masters, review, keep(saved))
    assert unchanged.revision == saved.revision
    removed = save(
        masters,
        review,
        [],
        edit=lambda filters: [v for v in filters if not v["community_source_id"]],
    )
    assert removed.community_sources == [] and community(removed) == {}
    assert removed.revision == saved.revision + 1
    with masters.app.accounts.factory() as db:
        assert db.scalars(select(ReviewCommunitySourceRow)).all() == []
        assert (
            db.scalars(
                select(ReviewFilterRow).where(ReviewFilterRow.community_source_id.is_not(None))
            ).all()
            == []
        )


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


def test_duplicates_copy_the_snapshot_and_its_filters(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    save(masters, review, include(link["linkId"]))
    saved = save(
        masters,
        review,
        None,
        edit=lambda filters: [
            {
                **v,
                "selected": v["community_filter_id"] != "f_b",
                "community_edited": v["community_filter_id"] == "f_b",
            }
            for v in filters
        ],
    )
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
        assert source.link_id == link_id and spans(copy) == spans(saved)
        assert source.source_id != saved.community_sources[0].source_id
        assert {value.community_source_id for value in community(copy).values()} == {
            source.source_id
        }
        assert community(copy)["f_b"].community_edited
        assert source.state == ("current" if link_id else "link_unavailable")


def test_account_deletion_keeps_snapshots_and_transfer_and_replace_repoint(masters, review):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = save(masters, review, include(link["linkId"]))
    masters.service.publish("v_2", 1, served_filter("f_z"))
    replaced = masters.apply(USER, "v_2", replace=True).json()["link"]
    with session(masters) as db:
        stored = get_saved_review(db, review)
    (source,) = stored.community_sources
    assert source.link_id == replaced["linkId"] and source.state == "changed"
    assert spans(stored) == spans(saved)

    peer = masters.link(PEER_USER, "v_1", "2", *FILTERS)
    with masters.app.accounts.factory() as db:
        transfer_master_state(db, USER, PEER_USER)
        db.commit()
    with masters.app.accounts.factory() as db:
        assert db.scalar(select(ReviewCommunitySourceRow.link_id)) == peer["linkId"]
        delete_master_state(db, PEER_USER)
        db.commit()
    with session(masters) as db:
        stored = get_saved_review(db, review)
    assert stored.community_sources[0].link_id is None
    assert stored.community_sources[0].state == "link_unavailable"
    assert spans(stored) == spans(saved)


def test_hide_downvoted_is_a_saved_per_account_preference(masters):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    saved = masters.ok("PATCH", "/preferences", USER, {"hide_downvoted": True})
    assert saved == {
        "version": 1,
        "confidenceThreshold": 0.0,
        "showLowConfidence": False,
        "hideDownvoted": True,
    }
    assert masters.ok("GET", "/preferences", PEER_USER)["hideDownvoted"] is False
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert view["preferences"]["hideDownvoted"] is True
    assert all(row["active"] for row in view["filters"])
    refused = masters.send("PATCH", "/preferences", USER, {"hide_downvoted": "yes"})
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


def test_filters_are_clipped_to_the_runtime_without_counting_as_a_change(masters, review):
    from scanner.db import ScanRow

    with masters.app.accounts.factory() as db:
        db.get(ScanRow, masters.scan_id).duration_seconds = 100
        db.get(ReviewSynchronizationRow, review).end_seconds = 100
        db.commit()
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter("f_tail", 95_000, 130_000),
        served_filter("f_after", 120_000, 130_000),
    )
    saved = save(masters, review, include(link["linkId"]))
    assert spans(saved) == [("f_tail", 95.0, 100.0, True)]
    assert not community(saved)["f_tail"].community_edited
    assert saved.community_sources[0].state == "current"
