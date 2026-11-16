from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from scanner.db import AuditEventRow
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.records import AUDIT_RETENTION, AuditEvent, AuditOutcome
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.audit import visibility
from scanner.security.audit.actions import ACTION_SPECS, ActorKind, AuditAction
from scanner.security.audit.query import AuditFilters, scoped_page
from scanner.security.audit.store import run_retention, stage_event
from tests.multi_user_access_support import build_access
from tests.test_audit_events import db as db

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def test_hidden_accounts_are_removed_before_counts_pages_and_filters(db):
    identifiers = []
    for target in [UserId(1), UserId(4), UserId(1), UserId(4)]:
        identifiers.append(
            stage_event(
                db,
                AuditEvent(
                    NOW,
                    None,
                    None,
                    AuditAction.SIGN_IN,
                    AuditOutcome.FAILED,
                    target_user=target,
                    details={"reason": "invalid_credentials"},
                ),
                actor_kind=ActorKind.ANONYMOUS,
            )
        )
    db.commit()
    accounts = {
        UserId(1): AccountRef(UserId(1), Role.OWNER),
        UserId(4): AccountRef(UserId(4), Role.USER),
    }
    access = build_access(Role.ADMIN)
    page = scoped_page(db, access, AuditFilters(page_size=1), accounts=accounts, media={}, now=NOW)
    assert page.total == 2
    assert page.pages == 2
    assert page.events[0].event_id == identifiers[3]
    hidden = scoped_page(
        db, access, AuditFilters(target_user_id=1), accounts=accounts, media={}, now=NOW
    )
    assert hidden.total == 0
    assert hidden.events == []


def test_owner_history_has_key_actions_but_never_credential_values(db):
    for action in (
        AuditAction.API_KEY_ISSUE,
        AuditAction.API_KEY_REVEAL,
        AuditAction.API_KEY_REVOKE,
    ):
        stage_event(
            db,
            AuditEvent(
                NOW,
                UserId(1),
                CredentialKind.SESSION,
                action,
                AuditOutcome.SUCCEEDED,
                target_user=UserId(4),
            ),
        )
    db.commit()
    page = scoped_page(
        db,
        build_access(Role.OWNER),
        AuditFilters(category="security"),
        accounts={UserId(4): AccountRef(UserId(4), Role.USER)},
        media={},
        now=NOW,
    )
    assert {entry.action for entry in page.events} == {
        AuditAction.API_KEY_ISSUE,
        AuditAction.API_KEY_REVEAL,
        AuditAction.API_KEY_REVOKE,
    }
    assert all(
        entry.details == {} and entry.credential == CredentialKind.SESSION for entry in page.events
    )
    assert '"password"' not in page.model_dump_json()


def test_retention_hides_boundary_before_cleanup_and_removes_it_after_refresh(db):
    identifiers = []
    cutoff = NOW - AUDIT_RETENTION
    for at in (cutoff, cutoff + timedelta(microseconds=1)):
        identifiers.append(
            stage_event(
                db,
                AuditEvent(
                    at,
                    None,
                    None,
                    AuditAction.SIGN_IN,
                    AuditOutcome.FAILED,
                    details={"reason": "invalid_credentials"},
                ),
                actor_kind=ActorKind.ANONYMOUS,
            )
        )
    db.commit()
    page = scoped_page(db, build_access(Role.OWNER), AuditFilters(), accounts={}, media={}, now=NOW)
    assert [entry.event_id for entry in page.events] == [identifiers[1]]
    assert run_retention(db, now=NOW + timedelta(microseconds=1)) == 2
    refreshed = scoped_page(
        db,
        build_access(Role.OWNER),
        AuditFilters(),
        accounts={},
        media={},
        now=NOW + timedelta(microseconds=1),
    )
    assert refreshed.total == 0
    assert db.scalar(select(AuditEventRow.id)) is None


def test_unvalidated_stored_details_never_leave_the_query(db):
    identifier = stage_event(
        db,
        AuditEvent(
            NOW,
            None,
            None,
            AuditAction.SIGN_IN,
            AuditOutcome.FAILED,
            details={"reason": "invalid_credentials"},
        ),
        actor_kind=ActorKind.ANONYMOUS,
    )
    db.execute(
        update(AuditEventRow)
        .where(AuditEventRow.event_id == identifier)
        .values(details_json='{"password":"SECRET-SENTINEL","reason":"invalid_credentials"}')
    )
    db.commit()
    page = scoped_page(db, build_access(Role.OWNER), AuditFilters(), accounts={}, media={}, now=NOW)
    assert page.events == []
    assert "SECRET-SENTINEL" not in page.model_dump_json()


@pytest.mark.parametrize(
    "values",
    [
        {"page": 0},
        {"page_size": 101},
        {"media_key": "/private/path"},
        {"since": "2026-10-22T12:00:00"},
        {"action": "unregistered"},
        {"since": "2026-10-23T00:00:00Z", "until": "2026-10-22T00:00:00Z"},
    ],
)
def test_query_filters_reject_ambiguous_or_unbounded_values(values):
    with pytest.raises(ValidationError):
        AuditFilters(**values)


def test_every_registered_audit_action_has_an_explicit_visibility_rule():
    assert set(visibility.ACTION_CAPABILITIES) == set(AuditAction) == set(ACTION_SPECS)


def test_missing_action_rule_is_owner_only(monkeypatch):
    rules = dict(visibility.ACTION_CAPABILITIES)
    rules.pop(AuditAction.LOGGING_CHANGE)
    monkeypatch.setattr(visibility, "ACTION_CAPABILITIES", rules)
    assert not visibility.action_visible(
        AuditAction.LOGGING_CHANGE, None, {}, build_access(Role.ADMIN)
    )
    assert visibility.action_visible(AuditAction.LOGGING_CHANGE, None, {}, build_access(Role.OWNER))


@pytest.mark.parametrize(
    ("action", "target", "details", "capability"),
    [
        (AuditAction.SIGN_IN, 4, {}, Capability.ACCOUNTS_LIST),
        (AuditAction.ACCOUNT_UPDATE, 4, {}, Capability.ACCOUNTS_PROFILE_EDIT_OTHER),
        (AuditAction.API_KEY_REVOKE, 4, {}, Capability.API_KEYS_REVOKE_OTHER),
        (AuditAction.API_KEY_REVOKE, 2, {}, Capability.API_KEYS_REVOKE_OWN),
        (AuditAction.API_KEY_ISSUE, 2, {"replaced": False}, Capability.API_KEYS_CREATE_OWN),
        (AuditAction.API_KEY_ISSUE, 2, {"replaced": True}, Capability.API_KEYS_ROTATE_OWN),
        (AuditAction.ACCOUNT_CHANGE, 2, {"changed": "username"}, Capability.PROFILE_EDIT_OWN),
        (
            AuditAction.ACCOUNT_CHANGE,
            2,
            {"changed": "password"},
            Capability.PROFILE_EDIT_OWN,
        ),
        (AuditAction.ACCOUNT_CREATE, 4, {"method": "managed"}, Capability.ACCOUNTS_JELLYFIN_IMPORT),
        (
            AuditAction.ACCOUNT_CREATE,
            4,
            {"method": "jellyfin_import"},
            Capability.ACCOUNTS_JELLYFIN_IMPORT,
        ),
        (AuditAction.ACCOUNT_STATUS, 4, {"status": "active"}, Capability.ACCOUNTS_ENABLE_OTHER),
        (AuditAction.ACCOUNT_STATUS, 4, {"status": "disabled"}, Capability.ACCOUNTS_DISABLE_OTHER),
        (
            AuditAction.EXPORT_PUBLISH,
            2,
            {"target_kind": "personal"},
            Capability.EXPORTS_PERSONAL_PUBLISH_OWN,
        ),
        (
            AuditAction.EXPORT_PUBLISH,
            None,
            {"target_kind": "item_default"},
            Capability.EXPORTS_DEFAULT_PUBLISH,
        ),
        (
            AuditAction.COMMUNITY_IDENTITY_CHANGE,
            4,
            {"change": "disconnect"},
            Capability.COMMUNITY_IDENTITY_DISCONNECT_OTHER,
        ),
        (
            AuditAction.MEDIA_ACCESS_CHANGE,
            4,
            {"change": "user_scope"},
            Capability.MEDIA_ACCESS_USER_EDIT,
        ),
    ],
)
def test_visibility_uses_the_governing_target_or_detail_capability(
    action, target, details, capability
):
    denied = build_access(Role.ADMIN, overrides={capability: OverrideEffect.DENY})
    allowed = build_access(Role.ADMIN, overrides={capability: OverrideEffect.ALLOW})
    assert not visibility.action_visible(action, target, details, denied)
    assert visibility.action_visible(action, target, details, allowed)


@pytest.mark.parametrize(
    ("action", "details"),
    [
        (AuditAction.CONNECTION_CHANGE, {}),
        (AuditAction.PERMISSIONS_CHANGE, {}),
        (AuditAction.FILTER_POLICY_CHANGE, {}),
        (AuditAction.REGISTRATION_CHANGE, {}),
        (AuditAction.JELLYFIN_ADMISSION_CHANGE, {}),
        (AuditAction.MEDIA_ACCESS_CHANGE, {"change": "exception_granted"}),
        (AuditAction.COMMUNITY_IDENTITY_CHANGE, {"change": "unknown"}),
        (AuditAction.ACCOUNT_CREATE, {}),
    ],
)
def test_owner_only_and_unmapped_variants_fail_closed(action, details):
    assert not visibility.action_visible(action, 4, details, build_access(Role.ADMIN))
    assert visibility.action_visible(action, 4, details, build_access(Role.OWNER))
