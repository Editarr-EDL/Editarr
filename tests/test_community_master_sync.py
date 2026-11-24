"""Daily community master sync state and notifications, per account (local API §8)."""

from datetime import timedelta

import pytest

from scanner.persistence.repository.community_master_sync import (
    begin_sync,
    finish_sync,
    links_to_sync,
    list_notifications,
    mark_notifications_read,
    record_notification,
    sync_status,
    unread_count,
)
from tests.community_master_support import (
    MEMBER,
    OWNER,
    SERVICE,
    T0,
    link,
    master_database,
    served,
)


@pytest.fixture
def db(tmp_path):
    engine, factory = master_database(tmp_path)
    with factory() as session:
        yield session
    engine.dispose()


def test_a_run_is_due_once_a_day_and_not_while_running(db):
    assert sync_status(db, OWNER, SERVICE, now=T0).due
    run = begin_sync(db, OWNER, SERVICE, contributor_id="c_1", now=T0)
    assert run and begin_sync(db, OWNER, SERVICE, contributor_id="c_1", now=T0) is None
    assert sync_status(db, OWNER, SERVICE, now=T0).state == "running"
    assert not finish_sync(db, OWNER, SERVICE, "another-run", now=T0)
    assert finish_sync(db, OWNER, SERVICE, run, now=T0)
    status = sync_status(db, OWNER, SERVICE, now=T0 + timedelta(hours=23))
    assert (status.state, status.due, status.last_run_at) == ("idle", False, T0)
    assert status.next_due_at == T0 + timedelta(hours=24)
    assert sync_status(db, OWNER, SERVICE, now=T0 + timedelta(hours=24)).due
    assert sync_status(db, MEMBER, SERVICE, now=T0).due


def test_a_failed_or_abandoned_run_stays_due(db):
    run = begin_sync(db, OWNER, SERVICE, contributor_id=None, now=T0)
    finish_sync(db, OWNER, SERVICE, run, error_code="service_unavailable", now=T0)
    status = sync_status(db, OWNER, SERVICE, now=T0)
    assert (status.state, status.last_error, status.due) == ("failed", "service_unavailable", True)
    begin_sync(db, OWNER, SERVICE, contributor_id=None, now=T0)
    assert begin_sync(db, OWNER, SERVICE, contributor_id=None, now=T0 + timedelta(hours=2))


def test_links_to_sync_are_the_accounts_active_links(db):
    created = link(db, served("f_a"))
    link(db, served("f_a"), owner=MEMBER)
    assert [item.id for item in links_to_sync(db, OWNER, SERVICE)] == [created.id]
    assert links_to_sync(db, OWNER, "https://other.example") == []


def test_notifications_are_recorded_once_listed_newest_first_and_read(db):
    created = link(db, served("f_a"))
    first = record_notification(
        db, OWNER, created.id, "changed", from_revision=1, to_revision=2, now=T0
    )
    again = record_notification(db, OWNER, created.id, "changed", from_revision=1, to_revision=2)
    assert again.id == first.id
    merged = record_notification(db, OWNER, created.id, "merged", now=T0 + timedelta(minutes=1))
    assert record_notification(db, OWNER, created.id, "merged").id == merged.id
    assert [row.id for row in list_notifications(db, OWNER)] == [merged.id, first.id]
    assert unread_count(db, OWNER) == 2 and list_notifications(db, MEMBER) == []
    with pytest.raises(ValueError):
        record_notification(db, MEMBER, created.id, "changed", to_revision=3)
    with pytest.raises(ValueError):
        record_notification(db, OWNER, created.id, "deleted")
    assert mark_notifications_read(db, MEMBER, all_=True) == 0
    assert mark_notifications_read(db, OWNER, [first.id], now=T0) == 1
    assert unread_count(db, OWNER) == 1
    assert mark_notifications_read(db, OWNER, all_=True, now=T0) == 1
    assert sync_status(db, OWNER, SERVICE, now=T0).unread_count == 0
    with pytest.raises(ValueError):
        mark_notifications_read(db, OWNER, [str(index) for index in range(101)])


def test_merged_and_changed_notifications_can_share_a_revision(db):
    created = link(db, served("f_a"))
    changed = record_notification(
        db, OWNER, created.id, "changed", from_revision=1, to_revision=2, now=T0
    )
    merged = record_notification(db, OWNER, created.id, "merged", to_revision=2, now=T0)
    db.commit()
    assert merged.id != changed.id
    assert record_notification(db, OWNER, created.id, "merged", to_revision=2).id == merged.id
    assert {row.kind for row in list_notifications(db, OWNER)} == {"changed", "merged"}
