"""Account deletion and transfer count, delete, and move community master-set state."""

import pytest
from sqlalchemy import func, select

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterPreferenceRow,
    CommunitySyncNotificationRow,
    CommunitySyncStateRow,
    MediaTitleRow,
    MediaVersionRow,
)
from scanner.persistence.repository.account_community_masters import (
    MasterImpact,
    delete_master_state,
    master_impact,
    transfer_master_state,
)
from scanner.persistence.repository.community_master_filters import set_preferences
from scanner.persistence.repository.community_master_links import LinkVersion, apply_master
from scanner.persistence.repository.community_master_sync import (
    begin_sync,
    finish_sync,
    record_notification,
)
from tests.community_master_support import (
    MEMBER,
    OWNER,
    SERVICE,
    T0,
    link,
    master_database,
    served,
    snapshot,
)
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_account_deletion_http import (
    delete as dispose,
)
from tests.test_account_deletion_http import (
    deletion_routes,
    exports_app,
    preview,
)
from tests.test_personal_exports_http import review_app

__all__ = ["exports_app", "review_app"]


@pytest.fixture
def db(tmp_path):
    engine, factory = master_database(tmp_path)
    with factory() as session:
        yield session
    engine.dispose()


def _seed(db, owner, *, threshold=0.25):
    created = link(db, served("f_a"), owner=owner)
    record_notification(db, owner, created.id, "changed", from_revision=1, to_revision=2, now=T0)
    set_preferences(db, owner, confidence_threshold=threshold)
    run = begin_sync(db, owner, SERVICE, contributor_id="c", now=T0)
    finish_sync(db, owner, SERVICE, run, now=T0)
    db.commit()
    return created


def _owned(db, row, owner):
    return db.scalar(select(func.count()).select_from(row).where(row.owner_id == owner))


def test_impact_counts_the_accounts_master_state(db):
    assert master_impact(db, OWNER) == MasterImpact()
    _seed(db, OWNER)
    assert master_impact(db, OWNER) == MasterImpact(1, 1, True, 1)
    assert master_impact(db, MEMBER) == MasterImpact()


def test_deletion_removes_only_the_accounts_master_state(db):
    _seed(db, OWNER)
    kept = _seed(db, MEMBER)
    assert delete_master_state(db, OWNER) == 1
    db.commit()
    assert master_impact(db, OWNER) == MasterImpact()
    assert master_impact(db, MEMBER) == MasterImpact(1, 1, True, 1)
    rows = db.scalars(select(CommunityMasterFilterRow.link_id)).all()
    assert rows == [kept.id]


def test_transfer_moves_state_without_overwriting_the_destinations(db):
    moved = _seed(db, OWNER)
    other = link(db, served("f_b"), version="v_2", replace=True)
    record_notification(db, OWNER, other.id, "changed", to_revision=3, now=T0)
    db.commit()
    assert other.link_state == "active" and moved.link_state == "replaced"
    own = _seed(db, MEMBER, threshold=0.75)
    assert transfer_master_state(db, OWNER, MEMBER) == 2
    db.commit()
    assert master_impact(db, OWNER) == MasterImpact()
    states = dict(
        db.execute(
            select(CommunityMasterLinkRow.id, CommunityMasterLinkRow.link_state).where(
                CommunityMasterLinkRow.owner_id == MEMBER
            )
        ).all()
    )
    assert states == {moved.id: "replaced", other.id: "replaced", own.id: "active"}
    assert _owned(db, CommunitySyncNotificationRow, MEMBER) == 3
    assert db.get(CommunityMasterPreferenceRow, MEMBER).confidence_threshold == 0.75
    assert _owned(db, CommunitySyncStateRow, MEMBER) == 1


def test_transfer_gives_state_to_a_destination_without_any(db):
    created = _seed(db, OWNER)
    transfer_master_state(db, OWNER, MEMBER)
    db.commit()
    assert db.get(CommunityMasterLinkRow, created.id).link_state == "active"
    assert master_impact(db, MEMBER) == MasterImpact(1, 1, True, 1)


@pytest.fixture
def app(exports_app, monkeypatch):
    return deletion_routes(exports_app, monkeypatch)


def _seed_http(app, owner):
    with app.accounts.factory() as session:
        if session.get(MediaVersionRow, "mv_local") is None:
            session.add(MediaTitleRow(id="title", entity_type="movie"))
            session.flush()
            session.add(MediaVersionRow(id="mv_local", title_id="title"))
            session.flush()
        created = apply_master(
            session,
            owner_id=owner,
            media_version_id="mv_local",
            service_url=SERVICE,
            contributor_id="c",
            version=LinkVersion("v_1", "confirmed", tier="2"),
            snapshot=snapshot(1, served("f_a")),
            alignment_required=False,
            now=T0,
        )
        record_notification(session, owner, created.id, "changed", to_revision=2, now=T0)
        set_preferences(session, owner, show_low_confidence=True)
        session.commit()
        return created.id


def test_http_deletion_previews_and_removes_master_state(app):
    _seed_http(app, USER)
    body = preview(app, ADMIN, USER, "delete").json()
    assert {key: body["community"][key] for key in body["community"] if "master" in key} == {
        "master_links": 1,
        "master_notifications": 1,
        "master_preferences": True,
        "master_sync_states": 0,
    }
    response = dispose(app, ADMIN, USER, "delete", body["confirmation"])
    assert response.status_code == 200, response.text
    assert response.json()["community_master_links_deleted"] == 1
    with app.accounts.factory() as session:
        assert master_impact(session, USER) == MasterImpact()


def test_http_transfer_moves_master_state(app):
    link_id = _seed_http(app, USER)
    body = preview(app, ADMIN, USER, "transfer", PEER_USER).json()
    assert body["community"]["master_links"] == 1
    response = dispose(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert response.status_code == 200, response.text
    assert response.json()["community_master_links_transferred"] == 1
    with app.accounts.factory() as session:
        assert session.get(CommunityMasterLinkRow, link_id).owner_id == PEER_USER
        assert master_impact(session, PEER_USER) == MasterImpact(1, 1, True, 0)
