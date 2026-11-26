"""Community master links, the read-only master view, votes and preferences, per account."""

import pytest
from sqlalchemy import select

from scanner.db import CommunityMasterLinkRow, CommunityMasterSnapshotRow
from scanner.persistence.repository.community_master_filters import (
    get_preferences,
    master_view,
    set_filter_vote,
    set_preferences,
)
from scanner.persistence.repository.community_master_links import (
    CommunityMasterError,
    approve_alignment,
    get_link,
    latest_snapshot,
    list_links,
    live_link,
    mark_link_state,
)
from tests.community_master_support import (
    INFO,
    MEMBER,
    OWNER,
    SERVICE,
    VERSION_ID,
    link,
    master_database,
    served,
    snapshot,
    values,
)


@pytest.fixture
def db(tmp_path):
    engine, factory = master_database(tmp_path)
    with factory() as session:
        yield session
    engine.dispose()


def _filters(db, link_id, owner=OWNER):
    return {view.filter_id: view for view in master_view(db, owner, link_id).filters}


def test_apply_stores_exact_snapshot_and_untouched_rows(db):
    low = served("f_low", values(30_000, 31_000, low_confidence=True))
    created = link(db, served("f_a"), low)
    stored = latest_snapshot(db, created)
    assert stored.snapshot_bytes == snapshot(1, served("f_a"), low).snapshot_bytes
    assert (stored.revision, stored.receipt_id) == (1, f"{1:064d}")
    rows = _filters(db, created.id)
    assert {name: row.active for name, row in rows.items()} == {"f_a": True, "f_low": False}
    assert rows["f_a"].master == {**INFO, **values().as_json()}


def test_links_are_scoped_to_their_account(db):
    created = link(db, served("f_a"))
    assert get_link(db, MEMBER, created.id) is None
    with pytest.raises(CommunityMasterError) as refused:
        master_view(db, MEMBER, created.id)
    assert refused.value.code == "not_found"
    assert list_links(db, MEMBER) == []
    assert [item.id for item in list_links(db, OWNER, [VERSION_ID])] == [created.id]
    other = link(db, served("f_a"), owner=MEMBER)
    assert other.id != created.id and live_link(db, MEMBER, VERSION_ID, SERVICE) is other


def test_fuzzy_link_rows_wait_for_alignment(db):
    created = link(db, served("f_a"), tier="4")
    assert created.alignment_state == "required"
    assert not _filters(db, created.id)["f_a"].active
    with pytest.raises(CommunityMasterError, match="update_changed"):
        approve_alignment(db, OWNER, created.id, {"offsetMs": 1300, "scale": 1.0}, 2)
    approve_alignment(db, OWNER, created.id, {"offsetMs": 1300, "scale": 1.0, "anchors": 3}, 1)
    row = _filters(db, created.id)["f_a"]
    assert row.active and (row.master["startMs"], row.master["endMs"]) == (11_300, 21_300)


def test_replacing_a_version_needs_confirmation_and_keeps_history(db):
    first = link(db, served("f_a"))
    with pytest.raises(CommunityMasterError, match="replace_required"):
        link(db, served("f_b"), version="v_2")
    second = link(db, served("f_b"), version="v_2", replace=True)
    assert db.get(CommunityMasterLinkRow, first.id).link_state == "replaced"
    assert list(_filters(db, first.id)) == ["f_a"]
    assert live_link(db, OWNER, VERSION_ID, SERVICE).id == second.id
    assert {item.id for item in list_links(db, OWNER, include_replaced=True)} == {
        first.id,
        second.id,
    }


def test_same_version_again_is_idempotent(db):
    created = link(db, served("f_a"))
    assert link(db, served("f_a")).id == created.id
    with pytest.raises(CommunityMasterError, match="snapshot_invalid"):
        link(db, served("f_other"))
    count = db.scalars(select(CommunityMasterSnapshotRow)).all()
    assert len(count) == 1


def test_votes_are_recorded_per_account(db):
    created = link(db, served("f_a"))
    assert set_filter_vote(db, OWNER, created.id, "f_a", "up").my_vote == "up"
    assert set_filter_vote(db, OWNER, created.id, "f_a", "none").my_vote is None
    with pytest.raises(CommunityMasterError):
        set_filter_vote(db, MEMBER, created.id, "f_a", "down")
    with pytest.raises(ValueError):
        set_filter_vote(db, OWNER, created.id, "f_a", "maybe")


def test_preferences_decide_which_filters_start_enabled(db):
    weak = served("f_weak", info={**INFO, "confidence": 0.2})
    low = served("f_low", values(30_000, 31_000, low_confidence=True))
    created = link(db, weak, low)
    assert get_preferences(db, OWNER) == get_preferences(db, MEMBER)
    assert _filters(db, created.id)["f_weak"].active
    set_preferences(db, OWNER, confidence_threshold=0.3)
    assert not _filters(db, created.id)["f_weak"].active
    assert not _filters(db, created.id)["f_low"].active
    set_preferences(db, OWNER, confidence_threshold=0.0, show_low_confidence=True)
    assert _filters(db, created.id)["f_low"].active
    with pytest.raises(ValueError):
        set_preferences(db, OWNER, confidence_threshold=1.5)


def test_overlap_with_a_local_model_finding_is_locally_verified(db):
    overlapping = served("f_hit", values(100_500, 102_000))
    other_category = served("f_miss", values(100_500, 102_000), category="drugs")
    created = link(db, overlapping, other_category, served("f_far"))
    rows = _filters(db, created.id)
    assert rows["f_hit"].locally_verified
    assert not rows["f_miss"].locally_verified and not rows["f_far"].locally_verified


def test_sync_can_mark_links_merged_or_unavailable(db):
    created = link(db, served("f_a"))
    merged = mark_link_state(db, OWNER, created.id, "merged", merged_into="v_9")
    assert (merged.link_state, merged.merged_into) == ("merged", "v_9")
    assert mark_link_state(db, OWNER, created.id, "unavailable").merged_into is None
    with pytest.raises(ValueError):
        mark_link_state(db, OWNER, created.id, "replaced")


def test_deleting_a_link_cascades_to_its_master_state(db):
    from sqlalchemy import func

    from scanner.db import (
        CommunityMasterDismissalRow,
        CommunityMasterFilterRow,
        CommunityMasterUpdateRow,
        CommunitySyncNotificationRow,
    )
    from scanner.persistence.repository.community_master_sync import record_notification
    from scanner.persistence.repository.community_master_updates import store_latest_snapshot

    created = link(db, served("f_a"), served("f_b"))
    store_latest_snapshot(db, OWNER, created.id, snapshot(2, served("f_a", values(1, 2))))
    record_notification(db, OWNER, created.id, "changed", from_revision=1, to_revision=2)
    db.commit()
    db.delete(db.get(CommunityMasterLinkRow, created.id))
    db.commit()
    for row in (
        CommunityMasterSnapshotRow,
        CommunityMasterFilterRow,
        CommunityMasterUpdateRow,
        CommunityMasterDismissalRow,
        CommunitySyncNotificationRow,
    ):
        assert db.scalar(select(func.count()).select_from(row)) == 0, row
