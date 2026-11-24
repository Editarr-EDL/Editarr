"""Storing newer master revisions and reviewing their update rows (local API §8, §9)."""

import pytest
from sqlalchemy import select

from scanner.db import (
    CommunityMasterDismissalRow,
    CommunityMasterFilterRow,
    CommunityMasterSnapshotRow,
)
from scanner.persistence.repository.community_master_filters import (
    master_view,
    set_filter_decision,
)
from scanner.persistence.repository.community_master_links import (
    CommunityMasterError,
    approve_alignment,
)
from scanner.persistence.repository.community_master_updates import (
    accept_all,
    decide_updates,
    master_update,
    pending_counts,
    store_latest_snapshot,
)
from tests.community_master_support import (
    INFO,
    MEMBER,
    OWNER,
    T0,
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


def _store(db, created, revision, *filters):
    return store_latest_snapshot(db, OWNER, created.id, snapshot(revision, *filters), now=T0)


def _rows(db, created):
    return {view.filter_id: view for view in master_view(db, OWNER, created.id).filters}


def _update(db, created):
    groups = master_update(db, OWNER, created.id).groups
    return {row.filter_id: row for rows in groups.values() for row in rows}


def test_confidence_only_change_applies_silently(db):
    created = link(db, served("f_a"))
    assert _store(db, created, 2, served("f_a", info={**INFO, "confidence": 0.9}))
    assert pending_counts(db, created.id) == {
        "new": 0,
        "changed": 0,
        "removed": 0,
        "nowLowConfidence": 0,
    }
    assert (created.applied_revision, created.latest_revision) == (2, 2)
    assert _rows(db, created)["f_a"].master["confidence"] == 0.9
    revisions = db.scalars(select(CommunityMasterSnapshotRow.revision)).all()
    assert revisions == [2]


def test_older_or_same_revision_is_not_newer(db):
    created = link(db, served("f_a"), revision=3)
    assert not _store(db, created, 2, served("f_b"))
    assert not _store(db, created, 3, served("f_a"))


def test_update_rows_never_change_what_is_in_effect(db):
    created = link(db, served("f_a"), served("f_gone"), served("f_low"))
    assert _store(
        db,
        created,
        2,
        served("f_a", values(10_500, 20_000)),
        served("f_low", values(low_confidence=True)),
        served("f_new", values(50_000, 51_000)),
    )
    assert pending_counts(db, created.id) == {
        "new": 1,
        "changed": 1,
        "removed": 1,
        "nowLowConfidence": 1,
    }
    rows = _rows(db, created)
    assert set(rows) == {"f_a", "f_gone", "f_low"}
    assert all(row.active for row in rows.values())
    assert rows["f_a"].effective["startMs"] == 10_000
    view = master_update(db, OWNER, created.id)
    assert (view.from_revision, view.to_revision, view.alignment_required) == (1, 2, False)
    changed = view.groups["changed"][0]
    assert (changed.changed_fields, changed.allowed, changed.can_accept, changed.mine) == (
        ("startMs",),
        ("accept", "dismiss"),
        True,
        None,
    )
    assert (changed.old_master["startMs"], changed.new_master["startMs"]) == (10_000, 10_500)
    assert view.groups["removed"][0].new_master is None


def test_accepting_every_row_applies_the_revision(db):
    created = link(db, served("f_a"), served("f_gone"), served("f_low"))
    _store(
        db,
        created,
        2,
        served("f_a", values(10_500, 20_000)),
        served("f_low", values(low_confidence=True)),
        served("f_new", values(50_000, 51_000)),
    )
    decide_updates(
        db,
        OWNER,
        created.id,
        2,
        [(name, "accept") for name in ("f_a", "f_gone", "f_low", "f_new")],
        now=T0,
    )
    rows = _rows(db, created)
    assert set(rows) == {"f_a", "f_low", "f_new"}
    assert rows["f_a"].effective["startMs"] == 10_500
    assert (rows["f_low"].state, rows["f_low"].active) == ("low_confidence", False)
    assert rows["f_new"].state == "pending" and rows["f_new"].active
    assert created.applied_revision == 2
    assert db.scalars(select(CommunityMasterSnapshotRow.revision)).all() == [2]


def test_touched_changed_row_keeps_mine_until_decided(db):
    created = link(db, served("f_a"))
    set_filter_decision(db, OWNER, created.id, "f_a", "adjust", {"endMs": 25_000})
    _store(db, created, 2, served("f_a", values(10_500, 20_000)))
    row = _update(db, created)["f_a"]
    assert (row.touched, row.allowed) == (True, ("keep_mine", "accept", "dismiss"))
    assert row.mine["endMs"] == 25_000
    before = _rows(db, created)["f_a"].effective
    assert (before["startMs"], before["endMs"]) == (10_000, 25_000)
    decide_updates(db, OWNER, created.id, 2, [("f_a", "keep_mine")], now=T0)
    kept = _rows(db, created)["f_a"]
    assert (kept.effective["startMs"], kept.effective["endMs"]) == (10_000, 25_000)
    assert (kept.master["startMs"], kept.state, kept.touched) == (10_500, "adjusted", True)


def test_accept_resets_only_the_changed_fields(db):
    created = link(db, served("f_a"))
    set_filter_decision(
        db, OWNER, created.id, "f_a", "adjust", {"startMs": 9_000, "severity": "low"}
    )
    _store(db, created, 2, served("f_a", values(10_500, 20_000)))
    decide_updates(db, OWNER, created.id, 2, [("f_a", "accept")], now=T0)
    row = _rows(db, created)["f_a"]
    assert (row.effective["startMs"], row.effective["severity"]) == (10_500, "low")
    assert (row.state, row.touched) == ("adjusted", True)


def test_touched_removed_row_stays_unless_accepted(db):
    created = link(db, served("f_a"), served("f_b"))
    for name in ("f_a", "f_b"):
        set_filter_decision(db, OWNER, created.id, name, "verify")
    _store(db, created, 2)
    assert {row.allowed for row in _update(db, created).values()} == {
        ("keep_mine", "accept", "dismiss")
    }
    assert all(row.active for row in _rows(db, created).values())
    decide_updates(db, OWNER, created.id, 2, [("f_a", "keep_mine"), ("f_b", "accept")], now=T0)
    rows = _rows(db, created)
    assert list(rows) == ["f_a"]
    assert rows["f_a"].active and rows["f_a"].master["present"] is False


def test_touched_now_low_confidence_keeps_mine(db):
    created = link(db, served("f_a"))
    set_filter_decision(db, OWNER, created.id, "f_a", "adjust", {"action": "mute"})
    _store(db, created, 2, served("f_a", values(low_confidence=True)))
    assert _update(db, created)["f_a"].allowed == ("accept", "dismiss")
    decide_updates(db, OWNER, created.id, 2, [("f_a", "accept")], now=T0)
    row = _rows(db, created)["f_a"]
    assert (row.state, row.effective["action"], row.active) == ("adjusted", "mute", True)
    assert row.master["lowConfidence"] is True


def test_dismissed_row_resurfaces_only_when_master_changes_again(db):
    created = link(db, served("f_a"))
    _store(db, created, 2, served("f_a", values(10_500, 20_000)))
    decide_updates(db, OWNER, created.id, 2, [("f_a", "dismiss")], now=T0)
    assert _rows(db, created)["f_a"].effective["startMs"] == 10_000
    assert created.applied_revision == 2
    _store(db, created, 3, served("f_a", values(10_500, 20_000), info={"confidence": 0.1}))
    assert _update(db, created) == {}
    assert db.scalars(select(CommunityMasterDismissalRow.filter_id)).all() == ["f_a"]
    _store(db, created, 4, served("f_a", values(10_700, 20_000)))
    assert _update(db, created)["f_a"].new_master["startMs"] == 10_700
    assert db.scalars(select(CommunityMasterDismissalRow)).all() == []


def test_accept_all_skips_touched_rows(db):
    created = link(db, served("f_a"), served("f_b"))
    set_filter_decision(db, OWNER, created.id, "f_b", "verify")
    _store(db, created, 2, served("f_a", values(1, 2)), served("f_b", values(1, 2)))
    remaining = accept_all(db, OWNER, created.id, 2, "changed", now=T0)
    assert [row.filter_id for row in remaining.groups["changed"]] == ["f_b"]
    assert created.applied_revision == 1
    with pytest.raises(CommunityMasterError, match="update_changed"):
        accept_all(db, OWNER, created.id, 1, "changed")


def test_fuzzy_links_accept_bounds_only_after_alignment_for_the_revision(db):
    created = link(db, served("f_a"), served("f_gone"), tier="4")
    approve_alignment(db, OWNER, created.id, {"offsetMs": 0, "scale": 1.0}, 1)
    _store(db, created, 2, served("f_a", values(1, 2)))
    rows = _update(db, created)
    assert (rows["f_a"].can_accept, rows["f_gone"].can_accept) == (False, True)
    with pytest.raises(CommunityMasterError, match="decision_not_allowed"):
        decide_updates(db, OWNER, created.id, 2, [("f_a", "accept")])
    assert accept_all(db, OWNER, created.id, 2, "changed").groups["changed"]
    approve_alignment(db, OWNER, created.id, {"offsetMs": 0, "scale": 1.0}, 2)
    assert master_update(db, OWNER, created.id).alignment_approved
    decide_updates(db, OWNER, created.id, 2, [("f_a", "accept")])


def test_decisions_are_atomic_and_checked(db):
    created = link(db, served("f_a"), served("f_b"))
    _store(db, created, 2, served("f_a", values(1, 2)), served("f_b", values(1, 2)))
    for decisions, code in (
        ([("f_a", "accept"), ("f_b", "keep_mine")], "decision_not_allowed"),
        ([("f_a", "accept"), ("f_a", "dismiss")], "decision_not_allowed"),
        ([("f_a", "accept"), ("f_missing", "accept")], "update_changed"),
    ):
        with pytest.raises(CommunityMasterError, match=code):
            decide_updates(db, OWNER, created.id, 2, decisions)
        assert set(_update(db, created)) == {"f_a", "f_b"}
    with pytest.raises(CommunityMasterError, match="update_changed"):
        decide_updates(db, OWNER, created.id, 1, [("f_a", "accept")])
    with pytest.raises(ValueError):
        decide_updates(db, OWNER, created.id, 2, [("f_a", "accept")] * 501)
    with pytest.raises(CommunityMasterError, match="not_found"):
        decide_updates(db, MEMBER, created.id, 2, [("f_a", "accept")])


def test_merged_rows_rekey_and_keep_local_state(db):
    created = link(db, served("f_1"), served("f_2"))
    set_filter_decision(db, OWNER, created.id, "f_2", "adjust", {"severity": "low"})
    _store(db, created, 2, served("f_m", merged=("f_1", "f_2")))
    stored = {row.filter_id for row in db.scalars(select(CommunityMasterFilterRow))}
    assert stored == {"f_1", "f_m"}
    row = _rows(db, created)["f_m"]
    assert (row.state, row.effective["severity"]) == ("adjusted", "low")
    removed = _update(db, created)["f_1"]
    assert (removed.group, removed.merged_into) == ("removed", "f_m")
