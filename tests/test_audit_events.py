"""MU-10 audit store: registry validation, scope checks, retries, and the 90-day boundary.

Uses a freshly migrated database with owner 1 (created by setup), synthetic
member 4, and one synthetic media item. Times come from a fixed clock.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from scanner.db import AuditEventRow, MediaItemRow, UserRow
from scanner.formats.export_layout import new_local_export_id
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.records import AUDIT_RETENTION, AuditEvent, AuditOutcome
from scanner.security.audit import store
from scanner.security.audit.actions import (
    ACTION_SPECS,
    ActorKind,
    AuditAction,
    AuditEventRejected,
)
from scanner.security.audit.checks import check_event
from scanner.security.audit.store import record_event, run_retention, stage_event
from tests.multi_user_access_support import add_linked_owner

_T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
OWNER, MEMBER, MISSING = UserId(1), UserId(4), UserId(99)
MEDIA = "media-0001"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with sessionmaker(bind=engine)() as session:
        add_linked_owner(session)
        session.add(
            UserRow(
                id=MEMBER,
                role="user",
                status="active",
                username="member4",
                normalized_username="member4",
                local_export_id=new_local_export_id().value,
            )
        )
        session.add(
            MediaItemRow(
                id=MEDIA,
                server_id="s" * 32,
                jellyfin_id="j" * 32,
                item_type="Movie",
                name="Synthetic movie",
            )
        )
        session.execute(text("DELETE FROM audit_events"))
        session.commit()
        yield session
    engine.dispose()


def _rows(db):
    return db.execute(
        select(
            AuditEventRow.action,
            AuditEventRow.outcome,
            AuditEventRow.actor_kind,
            AuditEventRow.actor_id,
            AuditEventRow.credential,
            AuditEventRow.target_user_id,
            AuditEventRow.media_key,
            AuditEventRow.resource,
            AuditEventRow.details_json,
        ).order_by(AuditEventRow.id)
    ).all()


def _export(**overrides) -> AuditEvent:
    values = dict(
        occurred_at=_T0,
        actor=MEMBER,
        credential=CredentialKind.SESSION,
        action=AuditAction.EXPORT_PUBLISH,
        outcome=AuditOutcome.SUCCEEDED,
        target_user=MEMBER,
        media_key=MEDIA,
        resource="review-0001",
        details={"format": "edl", "target_kind": "personal", "review_revision": 3},
    )
    return AuditEvent(**{**values, **overrides})


def _sign_in(at: datetime) -> AuditEvent:
    return AuditEvent(
        at,
        None,
        None,
        AuditAction.SIGN_IN,
        AuditOutcome.FAILED,
        target_user=OWNER,
        details={"reason": "invalid_credentials"},
    )


def _stored(at: datetime) -> datetime:
    return at.astimezone(timezone.utc).replace(tzinfo=None)


def test_stored_rows_carry_the_validated_actor_target_media_and_details(db):
    stage_event(db, _export())
    stage_event(
        db,
        _export(
            actor=None,
            credential=None,
            action=AuditAction.EXPORT_REGENERATE,
            target_user=None,
            details={"format": "mcf", "target_kind": "item_default", "policy_revision": 7},
        ),
        actor_kind=ActorKind.SYSTEM,
    )
    stage_event(
        db,
        AuditEvent(
            _T0,
            OWNER,
            CredentialKind.API_KEY,
            AuditAction.COMMUNITY_PUBLISH,
            AuditOutcome.DENIED,
            target_user=MEMBER,
            media_key=MEDIA,
            resource="op-0001",
            details={"kind": "other_review", "reason": "capability_denied"},
        ),
    )
    db.commit()

    assert _rows(db) == [
        (
            "export.publish", "succeeded", "user", 4, "session", 4, MEDIA, "review-0001",
            '{"format":"edl","review_revision":3,"target_kind":"personal"}',
        ),
        (
            "export.regenerate", "succeeded", "system", None, None, None, MEDIA, "review-0001",
            '{"format":"mcf","policy_revision":7,"target_kind":"item_default"}',
        ),
        (
            "community.publication.publish", "denied", "user", 1, "api_key", 4, MEDIA,
            "op-0001", '{"kind":"other_review","reason":"capability_denied"}',
        ),
    ]  # fmt: skip


_PERSONAL = {"format": "edl", "target_kind": "personal"}


@pytest.mark.parametrize(
    ("event", "actor_kind"),
    [
        pytest.param(_export(target_user=MISSING), ActorKind.USER, id="missing-target"),
        pytest.param(_export(actor=MISSING), ActorKind.USER, id="missing-actor"),
        pytest.param(_export(media_key="media-9999"), ActorKind.USER, id="missing-media"),
        pytest.param(_export(media_key=None), ActorKind.USER, id="export-without-media"),
        pytest.param(_export(resource=None), ActorKind.USER, id="export-without-resource"),
        pytest.param(_export(resource="a/b"), ActorKind.USER, id="resource-not-identifier"),
        pytest.param(_export(target_user=None), ActorKind.USER, id="personal-without-account"),
        pytest.param(
            _export(details={"format": "edl", "target_kind": "item_default"}),
            ActorKind.USER,
            id="item-default-naming-account",
        ),
        pytest.param(_export(details={"format": "edl"}), ActorKind.USER, id="missing-detail"),
        pytest.param(
            _export(details={**_PERSONAL, "format": "srt"}), ActorKind.USER, id="unlisted-value"
        ),
        pytest.param(
            _export(details={**_PERSONAL, "path": "/x"}), ActorKind.USER, id="unlisted-key"
        ),
        pytest.param(
            _export(details={**_PERSONAL, "review_revision": -1}),
            ActorKind.USER,
            id="negative-count",
        ),
        pytest.param(
            _export(details={**_PERSONAL, "review_revision": True}),
            ActorKind.USER,
            id="boolean-as-count",
        ),
        pytest.param(_export(action="export.copy"), ActorKind.USER, id="unregistered-action"),
        pytest.param(_export(), ActorKind.SYSTEM, id="system-naming-account"),
        pytest.param(_export(actor=None, credential=None), ActorKind.USER, id="user-without-id"),
        pytest.param(
            _export(actor=None, credential=None), ActorKind.ANONYMOUS, id="anonymous-export"
        ),
        pytest.param(
            _export(actor=None, credential=None), ActorKind.UNATTRIBUTED, id="new-unattributed"
        ),
        pytest.param(
            AuditEvent(
                _T0,
                OWNER,
                CredentialKind.SESSION,
                AuditAction.COMMUNITY_RETRACT,
                AuditOutcome.SUCCEEDED,
                target_user=OWNER,
                resource="op-1",
                details={"kind": "own_review"},
            ),
            ActorKind.USER,
            id="community-without-media",
        ),
        pytest.param(
            AuditEvent(
                _T0, None, None, AuditAction.SIGN_IN, AuditOutcome.SUCCEEDED, target_user=OWNER
            ),
            ActorKind.ANONYMOUS,
            id="anonymous-success",
        ),
        pytest.param(
            AuditEvent(
                _T0,
                OWNER,
                None,
                AuditAction.SIGN_IN,
                AuditOutcome.FAILED,
                details={"reason": "invalid_credentials"},
            ),
            ActorKind.USER,
            id="user-failing-sign-in",
        ),
    ],
)
def test_events_outside_the_registry_or_scope_are_refused_and_store_nothing(db, event, actor_kind):
    with pytest.raises(AuditEventRejected):
        stage_event(db, event, actor_kind=actor_kind)
    db.commit()

    assert _rows(db) == []


def test_every_export_and_community_action_is_registered_with_media_scope():
    families = {
        action: spec
        for action, spec in ACTION_SPECS.items()
        if action.value.split(".")[0] in {"export", "community"}
        and action is not AuditAction.COMMUNITY_IDENTITY_CHANGE
    }

    assert set(families) == {
        AuditAction.EXPORT_PUBLISH,
        AuditAction.EXPORT_REGENERATE,
        AuditAction.EXPORT_DELETE,
        AuditAction.EXPORT_RETAIN,
        AuditAction.EXPORT_WRITE_COMMITTED,
        AuditAction.COMMUNITY_PUBLISH,
        AuditAction.COMMUNITY_RETRY,
        AuditAction.COMMUNITY_RETRACT,
    }
    write_committed = families.pop(AuditAction.EXPORT_WRITE_COMMITTED)
    assert {spec.media.value for spec in families.values()} == {"required"}
    assert write_committed.media.value == "optional"
    identity = ACTION_SPECS[AuditAction.COMMUNITY_IDENTITY_CHANGE]
    assert (identity.target_user.value, identity.media.value) == ("required", "absent")
    assert set(ACTION_SPECS) == set(AuditAction)


def _write_committed(media_key=None, target_user=None, **details):
    outcome = AuditOutcome.FAILED if "reason" in details else AuditOutcome.SUCCEEDED
    return AuditEvent(
        _T0,
        OWNER,
        None,
        AuditAction.EXPORT_WRITE_COMMITTED,
        outcome,
        target_user=target_user,
        media_key=media_key,
        resource="export:9",
        details=details,
    )


@pytest.mark.parametrize(
    ("event", "allowed"),
    [
        (_write_committed(reason="not_found"), True),
        (_write_committed(reason="reauthentication_failed"), True),
        (_write_committed("media-1", None, format="edl", target_kind="item_default"), True),
        (_write_committed(format="edl", target_kind="item_default"), False),
        (_write_committed(target_user=OWNER, reason="not_found"), False),
        (_write_committed("media-1", reason="not_found"), False),
        (_write_committed(), False),
    ],
)
def test_a_write_committed_event_names_its_media_or_only_the_id(event, allowed):
    if allowed:
        assert check_event(event, ActorKind.USER) is AuditAction.EXPORT_WRITE_COMMITTED
    else:
        with pytest.raises(AuditEventRejected):
            check_event(event, ActorKind.USER)


def test_a_retry_with_the_same_event_id_stores_one_row(db):
    event_id = str(uuid.uuid4())
    for _ in range(2):
        assert record_event(db, _export(), event_id=event_id) is True

    assert db.scalar(select(AuditEventRow.event_id)) == event_id
    assert len(_rows(db)) == 1
    with pytest.raises(AuditEventRejected):
        stage_event(db, _export(), event_id=event_id.upper())
    with pytest.raises(AuditEventRejected):
        stage_event(db, _export(), request_id="request-1")


def test_the_ninety_day_boundary_expires_only_events_at_or_before_it(db):
    boundary = _T0 - AUDIT_RETENTION
    for at in (boundary - timedelta(days=1), boundary, boundary + timedelta(microseconds=1)):
        record_event(db, _sign_in(at), actor_kind=ActorKind.ANONYMOUS)
    assert len(_rows(db)) == 3

    record_event(db, _sign_in(_T0), actor_kind=ActorKind.ANONYMOUS)

    kept = {_stored(boundary + timedelta(microseconds=1)), _stored(_T0)}
    assert set(db.scalars(select(AuditEventRow.occurred_at))) == kept
    assert run_retention(db, now=_T0) == 0
    assert run_retention(db, now=_T0 + timedelta(microseconds=1)) == 1
    assert set(db.scalars(select(AuditEventRow.occurred_at))) == {_stored(_T0)}


def test_a_write_expires_old_events_only_when_its_transaction_commits(db):
    expired = _sign_in(_T0 - AUDIT_RETENTION - timedelta(days=5))
    record_event(db, expired, actor_kind=ActorKind.ANONYMOUS)
    stage_event(db, _sign_in(_T0), actor_kind=ActorKind.ANONYMOUS)
    db.rollback()

    assert len(_rows(db)) == 1
    stage_event(db, _sign_in(_T0), actor_kind=ActorKind.ANONYMOUS)
    db.commit()
    assert list(db.scalars(select(AuditEventRow.occurred_at))) == [_stored(_T0)]


def test_storage_failure_is_reported_without_payload_and_retention_retries(db, monkeypatch, caplog):
    def locked(*args, **kwargs):
        raise OperationalError("DELETE", {}, Exception("database is locked"))

    record_event(db, _sign_in(_T0 - AUDIT_RETENTION), actor_kind=ActorKind.ANONYMOUS)
    with monkeypatch.context() as patch:
        patch.setattr(store, "expire_events", locked)
        assert record_event(db, _sign_in(_T0), actor_kind=ActorKind.ANONYMOUS) is False
        assert run_retention(db, now=_T0) is None

    assert len(_rows(db)) == 1
    assert "invalid_credentials" not in caplog.text
    assert run_retention(db, now=_T0) == 1
    assert _rows(db) == []


def test_details_are_serialized_exactly_as_registered(db):
    record_event(db, _sign_in(_T0), actor_kind=ActorKind.ANONYMOUS)

    assert json.loads(db.scalar(select(AuditEventRow.details_json))) == {
        "reason": "invalid_credentials"
    }


def test_startup_revokes_sessions_of_a_replaced_csrf_key_only_with_their_event(
    tmp_path, monkeypatch
):
    from scanner import api
    from scanner.security.audit.authentication import sessions_revoked_by_key_change
    from scanner.security.auth_repository_sessions import revoke_sessions_with_other_csrf_key_id
    from tests.multi_user_access_support import live_accounts
    from tests.test_startup_gate import _configure_startup

    accounts = live_accounts(tmp_path)
    _configure_startup(monkeypatch, tmp_path, setup_required=True, calls=[])
    monkeypatch.setattr(api, "get_session_factory", lambda: accounts.factory)
    monkeypatch.setattr(api, "configure_runtime_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "run_audit_retention", run_retention)
    monkeypatch.setattr(api, "record_key_change_revocation", sessions_revoked_by_key_change)
    monkeypatch.setattr(
        api, "revoke_sessions_with_other_csrf_key_id", revoke_sessions_with_other_csrf_key_id
    )
    active = text("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL")
    revocations = select(AuditEventRow.actor_kind, AuditEventRow.details_json).where(
        AuditEventRow.action == "sessions.revoke"
    )
    try:
        with accounts.engine.begin() as connection:
            sessions = connection.execute(active).scalar()
            connection.execute(
                text(
                    "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events "
                    "BEGIN SELECT RAISE(ABORT, 'audit storage fault'); END"
                )
            )

        with pytest.raises(IntegrityError):
            api.startup()
        with accounts.engine.begin() as connection:
            assert connection.execute(active).scalar() == sessions > 0
            assert connection.execute(revocations).all() == []
            connection.execute(text("DROP TRIGGER fail_audit"))

        api.startup()
        with accounts.engine.connect() as connection:
            assert connection.execute(active).scalar() == 0
            assert connection.execute(revocations).all() == [
                ("system", f'{{"reason":"csrf_key_changed","sessions_revoked":{sessions}}}')
            ]
    finally:
        accounts.close()
