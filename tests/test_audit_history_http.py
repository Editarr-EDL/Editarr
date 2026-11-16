from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from scanner.db import MediaItemRow
from scanner.http import audit_history_api
from scanner.persistence.repository import media_access
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import CredentialKind
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.vocabulary import Role
from scanner.security.audit import history as audit_history
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.store import run_retention, stage_event
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import ADMIN, OWNER, USER, live_accounts

NOW = datetime.now(timezone.utc)
VISIBLE_LIBRARY = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
HIDDEN_LIBRARY = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"


@pytest.fixture
def history(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    client = serve_access_fixture(monkeypatch, accounts)
    client.app.include_router(audit_history_api.router)
    monkeypatch.setattr(audit_history_api, "session_now", lambda: NOW)
    with accounts.factory() as db:
        identifiers = {}
        for target in (OWNER, USER):
            identifiers[target] = stage_event(
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
        db.commit()
    yield client, accounts, identifiers
    client.close()
    accounts.close()


def get(history, actor, path, **params):
    client, accounts, _identifiers = history
    client.cookies.clear()
    return client.get(path, headers=accounts.cookie(actor), params=params)


def test_list_detail_filters_and_guessed_ids_have_the_same_scope(history):
    _client, _accounts, identifiers = history
    response = get(history, ADMIN, "/api/audit", page_size=1)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["total"] == 1
    assert response.json()["pages"] == 1
    assert response.json()["events"][0]["event_id"] == identifiers[USER]
    assert get(history, ADMIN, "/api/audit", target_user_id=OWNER).json()["total"] == 0
    assert get(history, ADMIN, f"/api/audit/{identifiers[OWNER]}").status_code == 404
    assert get(history, ADMIN, "/api/audit/00000000-0000-0000-0000-000000000000").status_code == 404
    assert get(history, ADMIN, f"/api/audit/{identifiers[USER]}").status_code == 200
    assert get(history, OWNER, f"/api/audit/{identifiers[OWNER]}").status_code == 200


def test_ordinary_users_are_refused_before_any_history_is_returned(history):
    assert get(history, USER, "/api/audit").status_code == 403
    assert get(history, USER, f"/api/audit/{history[2][USER]}").status_code == 403


def test_media_scope_is_applied_before_filters_pages_and_detail(history, tmp_path):
    _client, accounts, _identifiers = history
    with accounts.factory() as db:
        media_access.replace_scope(
            db,
            ADMIN,
            all_sources=False,
            sources=frozenset({(MediaSourceKind.JELLYFIN_LIBRARY, VISIBLE_LIBRARY)}),
            now=NOW,
        )
        ids = {}
        for key, library, item in (
            ("visible-media", VISIBLE_LIBRARY, "c3000000000000000000000000000001"),
            ("hidden-media", HIDDEN_LIBRARY, "c3000000000000000000000000000002"),
        ):
            path = tmp_path / f"{key}.mkv"
            path.write_bytes(b"synthetic")
            db.add(
                MediaItemRow(
                    id=key,
                    server_id="http://jellyfin.test",
                    jellyfin_id=item,
                    library_id=library,
                    item_type="Movie",
                    name=key,
                    path=str(path),
                )
            )
            db.flush()
            ids[key] = stage_event(
                db,
                AuditEvent(
                    NOW,
                    OWNER,
                    CredentialKind.SESSION,
                    AuditAction.EXPORT_PUBLISH,
                    AuditOutcome.SUCCEEDED,
                    target_user=USER,
                    media_key=key,
                    resource="review-1",
                    details={"format": "edl", "target_kind": "personal"},
                ),
            )
        db.commit()
    page = get(history, ADMIN, "/api/audit", category="export", page_size=1).json()
    assert page["total"] == page["pages"] == 1
    assert page["events"][0]["media_key"] == "visible-media"
    assert str(tmp_path) not in str(page)
    assert get(history, ADMIN, "/api/audit", media_key="hidden-media").json()["total"] == 0
    assert get(history, ADMIN, f"/api/audit/{ids['hidden-media']}").status_code == 404
    assert get(history, OWNER, f"/api/audit/{ids['hidden-media']}").status_code == 200
    with accounts.factory() as db:
        media_access.replace_scope(db, ADMIN, all_sources=False, sources=frozenset(), now=NOW)
        db.commit()
    assert get(history, ADMIN, "/api/audit", category="export").json()["total"] == 0


def test_capability_and_target_protection_changes_affect_existing_session(history):
    _client, accounts, identifiers = history
    accounts.set_override(ADMIN, Capability.AUDIT_READ, OverrideEffect.DENY)
    assert get(history, ADMIN, "/api/audit").status_code == 403
    assert get(history, ADMIN, f"/api/audit/{identifiers[USER]}").status_code == 403


@pytest.mark.parametrize("detail", [False, True])
@pytest.mark.parametrize("change", ["capability", "protected_target"])
def test_authority_change_before_read_lock_does_not_return_stale_history(
    history, monkeypatch, detail, change
):
    _client, accounts, identifiers = history
    original = audit_history.fresh_authority_write
    changed = False

    @contextmanager
    def racing_boundary(db, prepare, reload_state, unavailable):
        def prepared():
            nonlocal changed
            evidence = prepare()
            if not changed:
                changed = True
                if change == "capability":
                    accounts.set_override(ADMIN, Capability.AUDIT_READ, OverrideEffect.DENY)
                else:
                    accounts.set_role(USER, Role.ADMIN)
            return evidence

        with original(db, prepared, reload_state, unavailable) as evidence:
            yield evidence

    monkeypatch.setattr(audit_history, "fresh_authority_write", racing_boundary)
    path = f"/api/audit/{identifiers[USER]}" if detail else "/api/audit"
    response = get(history, ADMIN, path)
    if change == "capability":
        assert response.status_code == 403
    elif detail:
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["events"] == []


def test_expired_or_removed_events_cannot_be_reopened_from_another_session(history, monkeypatch):
    _client, accounts, identifiers = history
    assert get(history, OWNER, f"/api/audit/{identifiers[USER]}").status_code == 200
    future = NOW + timedelta(days=91)
    monkeypatch.setattr(audit_history_api, "session_now", lambda: future)
    assert get(history, ADMIN, "/api/audit").json()["total"] == 0
    assert get(history, OWNER, f"/api/audit/{identifiers[USER]}").status_code == 404
    with accounts.factory() as db:
        assert run_retention(db, now=future) >= 2
    assert get(history, ADMIN, f"/api/audit/{identifiers[USER]}").status_code == 404


@pytest.mark.parametrize(
    "params", [{"page": "0"}, {"page_size": "101"}, {"outcome": "secret"}, {"unknown": "x"}]
)
def test_invalid_filters_are_refused_without_history(history, params):
    response = get(history, OWNER, "/api/audit", **params)
    assert response.status_code == 422
    assert "events" not in response.json()


def _stage_operation(accounts, action, *, details=None, resource=None):
    with accounts.factory() as db:
        identifier = stage_event(
            db,
            AuditEvent(
                NOW,
                OWNER,
                CredentialKind.SESSION,
                action,
                AuditOutcome.SUCCEEDED,
                resource=resource,
                details=details or {},
            ),
        )
        db.commit()
    return identifier


def _race_prepare(monkeypatch, mutate):
    original = audit_history.fresh_authority_write

    @contextmanager
    def boundary(db, prepare, reload_state, unavailable):
        def prepared():
            result = prepare()
            mutate()
            return result

        with original(db, prepared, reload_state, unavailable) as evidence:
            yield evidence

    monkeypatch.setattr(audit_history, "fresh_authority_write", boundary)


@pytest.mark.parametrize(
    ("action", "details", "resource"),
    [
        (AuditAction.DEPLOYMENT_CHANGE, {"base_url": True}, None),
        (AuditAction.BACKUP_DOWNLOAD, {"kind": "full"}, "job-1"),
        (AuditAction.BACKUP_RESTORE, {"stage": "activate"}, "job-1"),
    ],
)
def test_admin_cannot_see_owner_only_operations(history, action, details, resource):
    identifier = _stage_operation(history[1], action, details=details, resource=resource)
    page = get(history, ADMIN, "/api/audit", action=action, page_size=1).json()
    assert (page["events"], page["total"], page["pages"]) == ([], 0, 0)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
    assert get(history, OWNER, f"/api/audit/{identifier}").status_code == 200


def test_action_capability_override_deny_and_allow_change_live_visibility(history):
    accounts = history[1]
    identifier = _stage_operation(accounts, AuditAction.LOGGING_CHANGE)
    path = f"/api/audit/{identifier}"
    assert get(history, ADMIN, path).status_code == 200
    accounts.set_override(ADMIN, Capability.LOGS_SETTINGS_EDIT, OverrideEffect.DENY)
    assert get(history, ADMIN, path).status_code == 404
    page = get(history, ADMIN, "/api/audit", action=AuditAction.LOGGING_CHANGE).json()
    assert (page["events"], page["total"], page["pages"]) == ([], 0, 0)
    accounts.set_override(ADMIN, Capability.LOGS_SETTINGS_EDIT, OverrideEffect.ALLOW)
    assert get(history, ADMIN, path).status_code == 200
    assert get(history, ADMIN, "/api/audit", action=AuditAction.LOGGING_CHANGE).json()["total"] == 1


@pytest.mark.parametrize("actor", [OWNER, ADMIN])
@pytest.mark.parametrize("detail", [False, True])
def test_unrelated_audit_write_during_prepare_does_not_starve_history(
    history, monkeypatch, actor, detail
):
    identifier = history[2][USER]
    _race_prepare(
        monkeypatch,
        lambda: _stage_operation(history[1], AuditAction.LOGGING_CHANGE),
    )
    response = get(history, actor, f"/api/audit/{identifier}" if detail else "/api/audit")
    assert response.status_code == 200
    assert identifier in response.text
    if not detail:
        assert AuditAction.LOGGING_CHANGE in {row["action"] for row in response.json()["events"]}


def test_continuous_visibility_changes_return_retryable_service_unavailable(history, monkeypatch):
    calls = []

    def change_authority():
        calls.append(1)
        history[1].set_override(
            ADMIN,
            Capability.LOGS_SETTINGS_EDIT,
            OverrideEffect.DENY if len(calls) % 2 else OverrideEffect.ALLOW,
        )

    _race_prepare(monkeypatch, change_authority)
    response = get(history, ADMIN, "/api/audit")
    assert len(calls) == 3
    assert response.status_code == 503
    assert response.json()["code"] == "audit_history_busy"
    assert response.headers["retry-after"] == "1"
    assert "no-store" in response.headers["cache-control"]
    assert "events" not in response.json()


def test_action_visibility_inherits_live_role_defaults(history):
    accounts = history[1]
    identifier = _stage_operation(accounts, AuditAction.LOGGING_CHANGE)
    path = f"/api/audit/{identifier}"
    accounts.set_role_default(Role.ADMIN, Capability.LOGS_SETTINGS_EDIT, False)
    assert get(history, ADMIN, path).status_code == 404
    accounts.set_override(ADMIN, Capability.LOGS_SETTINGS_EDIT, OverrideEffect.ALLOW)
    assert get(history, ADMIN, path).status_code == 200
    accounts.set_override(ADMIN, Capability.LOGS_SETTINGS_EDIT, OverrideEffect.INHERIT)
    assert get(history, ADMIN, path).status_code == 404
    accounts.set_role_default(Role.ADMIN, Capability.LOGS_SETTINGS_EDIT, True)
    assert get(history, ADMIN, path).status_code == 200


@pytest.mark.parametrize("detail", [False, True])
def test_governing_capability_revoked_during_prepare_hides_the_event(history, monkeypatch, detail):
    accounts = history[1]
    identifier = _stage_operation(accounts, AuditAction.LOGGING_CHANGE)
    changed = False

    def revoke_once():
        nonlocal changed
        if not changed:
            changed = True
            accounts.set_override(ADMIN, Capability.LOGS_SETTINGS_EDIT, OverrideEffect.DENY)

    _race_prepare(monkeypatch, revoke_once)
    path = f"/api/audit/{identifier}" if detail else "/api/audit"
    response = get(
        history, ADMIN, path, **({} if detail else {"action": "settings.logging.change"})
    )
    if detail:
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert response.json()["total"] == 0
    assert identifier not in response.text
