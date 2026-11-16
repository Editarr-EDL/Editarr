import json
import logging
import stat
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner.db import Base
from scanner.security.auth import logout
from scanner.security.auth_audit import AuditContext, AuthenticationAuditLogger, new_request_id
from scanner.security.auth_repository import authenticate_session, issue_session
from tests.multi_user_access_support import add_linked_owner

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_logout_revokes_one_session_and_keeps_the_same_users_other_devices(db_session, tmp_path):
    owner = add_linked_owner(db_session)
    signed_in = issue_session(db_session, remember_me=False, user_id=owner, now=_NOW)
    other_device = issue_session(db_session, remember_me=True, user_id=owner, now=_NOW)
    current = authenticate_session(db_session, signed_in.token, now=_NOW)
    audit = AuthenticationAuditLogger(tmp_path, now=_NOW)

    assert logout(db_session, current, audit=audit, now=_NOW)
    assert not logout(db_session, current, audit=audit, now=_NOW)
    assert authenticate_session(db_session, signed_in.token, now=_NOW) is None
    assert authenticate_session(db_session, other_device.token, now=_NOW) is not None
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "auth-audit.jsonl").read_text().splitlines()
    ]
    assert events[-2:] == ["auth.logout", "auth.session_rejected"]


def test_audit_records_all_event_names_with_exact_schema_and_null_fields(tmp_path):
    audit = AuthenticationAuditLogger(tmp_path, now=_NOW)
    assert logging.getLogger("scanner.security.auth.audit").propagate is False
    context = AuditContext(source="system", request_id=new_request_id())
    event_names = (
        "auth.setup",
        "auth.login",
        "auth.login_throttled",
        "auth.logout",
        "auth.account_changed",
        "auth.security_changed",
        "auth.sessions_revoked",
        "auth.session_rejected",
        "auth.csrf_rejected",
        "auth.origin_rejected",
    )
    for event in event_names:
        audit.emit(
            event=event,
            outcome="failure" if event == "auth.login" else "denied",
            severity="warning",
            context=context,
            actor="anonymous",
            reason_code="invalid_credentials" if event == "auth.login" else None,
            now=_NOW,
        )

    audit_lines = (tmp_path / "auth-audit.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in audit_lines]
    required_keys = {
        "schema_version", "occurred_at", "event_id", "request_id", "application", "event",
        "outcome", "severity", "source", "client_address", "actor", "admin_user_id",
        "session_id", "http_method", "route", "http_status", "reason_code", "changed_fields",
    }
    assert {record["event"] for record in records} == {"auth.audit_started", *event_names}
    for record in records:
        assert set(record) == required_keys
        assert record["application"] == "editarr"
        assert record["changed_fields"] == []
        assert record["client_address"] is None
        assert record["http_method"] is None
        assert record["route"] is None
        assert record["http_status"] is None


def test_audit_rejects_unallowlisted_values_before_they_can_inject_jsonl(tmp_path):
    audit = AuthenticationAuditLogger(tmp_path, now=_NOW)
    before = (tmp_path / "auth-audit.jsonl").read_text()
    with pytest.raises(ValueError, match="route is not allowlisted"):
        AuditContext(
            source="http",
            request_id=new_request_id(),
            client_address="127.0.0.1",
            http_method="POST",
            route="/api/auth/login\n{\"event\":\"forged\"}",
            http_status=401,
        )
    with pytest.raises(ValueError, match="reason is not allowlisted"):
        audit.emit(
            event="auth.login",
            outcome="failure",
            severity="warning",
            context=AuditContext(source="system", request_id=new_request_id()),
            actor="anonymous",
            reason_code="forged\nreason",
            now=_NOW,
        )
    assert (tmp_path / "auth-audit.jsonl").read_text() == before


def test_audit_rotation_permissions_retention_and_destination_failure_are_safe(tmp_path, caplog):
    audit = AuthenticationAuditLogger(tmp_path, now=_NOW)
    audit.emit(
        event="auth.login",
        outcome="failure",
        severity="warning",
        context=AuditContext(source="system", request_id=new_request_id()),
        actor="anonymous",
        reason_code="invalid_credentials",
        now=_NOW + timedelta(days=1),
    )
    logs = tmp_path
    active = logs / "auth-audit.jsonl"
    rotated = logs / "auth-audit-2026-01-01.jsonl"
    old = logs / "auth-audit-2025-11-30.jsonl"
    old.write_text("old\n")
    AuthenticationAuditLogger(tmp_path, now=_NOW + timedelta(days=1))

    assert rotated.exists()
    assert not old.exists()
    assert stat.S_IMODE(logs.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(active.stat().st_mode) & 0o077 == 0

    blocked_data_dir = tmp_path / "blocked"
    blocked_data_dir.write_text("not a directory")
    broken = AuthenticationAuditLogger(blocked_data_dir, now=_NOW)
    broken.emit(
        event="auth.login",
        outcome="failure",
        severity="warning",
        context=AuditContext(source="system", request_id=new_request_id()),
        actor="anonymous",
        reason_code="invalid_credentials",
        now=_NOW,
    )
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
