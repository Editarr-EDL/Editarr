"""Following newer master revisions: a link always shows its latest snapshot (local API §8,
ADR 0002 U3)."""

import json

import pytest
from sqlalchemy import select

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterSnapshotRow,
    CommunityMasterUpdateRow,
)
from scanner.persistence.repository.community_master_filters import master_view
from scanner.persistence.repository.community_master_links import (
    approve_alignment,
    store_snapshot,
)
from scanner.persistence.repository.community_master_updates import (
    catch_up_link,
    store_latest_snapshot,
)
from tests.community_master_support import (
    INFO,
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


def test_a_newer_revision_applies_at_once(db):
    created = link(db, served("f_a"), served("f_gone"))
    assert _store(
        db,
        created,
        2,
        served("f_a", values(11_000, 21_000, severity="low"), info={**INFO, "confidence": 0.9}),
        served("f_new", values(40_000, 41_000)),
    )
    rows = _rows(db, created)
    assert sorted(rows) == ["f_a", "f_new"]
    assert (rows["f_a"].master["startMs"], rows["f_a"].master["severity"]) == (11_000, "low")
    assert rows["f_a"].master["confidence"] == 0.9
    assert (created.applied_revision, created.latest_revision) == (2, 2)
    gone = db.get(CommunityMasterFilterRow, (created.id, "f_gone"))
    assert not gone.present and json.loads(gone.baseline_json)["present"] is False
    assert db.scalars(select(CommunityMasterSnapshotRow.revision)).all() == [2]


def test_older_or_same_revision_is_not_newer(db):
    created = link(db, served("f_a"), revision=3)
    assert not _store(db, created, 2, served("f_b"))
    assert not _store(db, created, 3, served("f_a"))
    assert sorted(_rows(db, created)) == ["f_a"]


def test_a_merged_filter_continues_the_lowest_row_it_names(db):
    created = link(db, served("f_b"), served("f_c"), served("f_keep"))
    db.get(CommunityMasterFilterRow, (created.id, "f_c")).my_vote = "up"
    _store(db, created, 2, served("f_m", merged=("f_c", "f_b")), served("f_keep"))
    rows = {row.filter_id: row for row in db.scalars(select(CommunityMasterFilterRow))}
    assert rows["f_m"].present and rows["f_c"].my_vote == "up" and not rows["f_c"].present
    assert sorted(_rows(db, created)) == ["f_keep", "f_m"]


def test_a_fuzzy_link_keeps_its_approved_alignment_for_new_revisions(db):
    created = link(db, served("f_a"), tier="4")
    approve_alignment(db, OWNER, created.id, {"offsetMs": 1000, "scale": 1.0, "anchors": 1}, 1)
    _store(db, created, 2, served("f_a", values(12_000, 22_000)))
    row = _rows(db, created)["f_a"]
    assert row.active and (row.master["startMs"], row.master["endMs"]) == (13_000, 23_000)


def test_a_link_left_behind_by_an_earlier_build_catches_up(db):
    created = link(db, served("f_a"))
    newer = snapshot(2, served("f_a", values(5_000, 6_000)), served("f_b"))
    store_snapshot(db, created, newer, T0)
    created.latest_revision = 2
    db.add(
        CommunityMasterUpdateRow(
            link_id=created.id,
            filter_id="f_b",
            to_revision=2,
            change="new",
            changed_fields_json="[]",
            new_master_json="{}",
            values_digest="0" * 64,
        )
    )
    db.flush()
    assert catch_up_link(db, created, T0)
    assert created.applied_revision == 2
    assert _rows(db, created)["f_a"].master["startMs"] == 5_000
    assert sorted(_rows(db, created)) == ["f_a", "f_b"]
    assert not catch_up_link(db, created, T0)
