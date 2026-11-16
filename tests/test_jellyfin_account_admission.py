"""Admission tests: import and every first sign-in create one account per Jellyfin user.

Jellyfin administrator status sets only the starting role (JF-05); the owner's
later choice survives every sign-in and sync.

Jellyfin is the MU-08 `JellyfinBoundary` (fixture `editarr-jellyfin-users-1`)
over local HTTP, a labelled substitute for a live server. Every time comes from
a fixed clock; nothing sleeps.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select

from scanner.db import (
    AuditEventRow,
    JellyfinAccountSyncRow,
    UserFilterEnforcementRow,
    UserFilterPreferenceRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.models import FilterDefaultsUpdate
from scanner.persistence.repository.filter_enforcement import set_enforcement
from scanner.persistence.repository.filter_preferences import (
    filter_preferences_view,
    set_default_mode,
)
from scanner.persistence.repository.repository_settings import replace_filter_defaults
from scanner.persistence.rows.db_auth import UserProfileRow
from scanner.security import account_permissions
from scanner.security.access.vocabulary import Role
from scanner.security.jellyfin_accounts.admission import (
    ImportOutcome,
    SignInRefusal,
    SignInRefused,
)
from scanner.settings.filter_policy.updates import FilterPolicyUpdate
from tests.filter_policy_http_support import rule, severities
from tests.jellyfin_user_boundary import IDS, NOW
from tests.multi_user_access_support import USER
from tests.test_jellyfin_account_sync import _count, _import, _linked, _row, _sign_in, _sync
from tests.test_jellyfin_account_sync import accounts as accounts
from tests.test_jellyfin_account_sync import boundary as boundary
from tests.test_jellyfin_account_sync import clock as clock


def test_import_maps_roles_and_profiles_and_repeats_without_duplicates(accounts, boundary, clock):
    results = _import(accounts, boundary, clock, frozenset({IDS["Avery"], IDS["Blake"]}))

    assert [(r.name, r.outcome) for r in results] == [
        ("Avery", ImportOutcome.IMPORTED),
        ("Blake", ImportOutcome.IMPORTED),
    ]
    avery, blake = (_linked(accounts, name) for name in ("Avery", "Blake"))
    assert _row(accounts, UserRow, avery).role == "admin"
    assert _row(accounts, UserRow, blake).role == "user"
    assert _row(accounts, UserProfileRow, avery).display_name == "Avery"
    sync = _row(accounts, JellyfinAccountSyncRow, avery)
    assert (sync.upstream_state, sync.last_outcome, sync.is_administrator) == (
        "enabled",
        "verified",
        True,
    )
    identity = _row(accounts, UserProviderIdentityRow, avery)
    assert (identity.link_state, identity.last_verified_at) == (
        "verified",
        NOW.replace(tzinfo=None),
    )

    repeated = _import(accounts, boundary, clock)
    assert {r.name: r.outcome for r in repeated} == {
        "Avery": ImportOutcome.EXISTING,
        "Blake": ImportOutcome.EXISTING,
        "Casey": ImportOutcome.IMPORTED,
        "Drew": ImportOutcome.IMPORTED,
        "Emery": ImportOutcome.IMPORTED,
    }
    assert _count(accounts) == 5
    with accounts.factory() as db:
        methods = db.scalars(
            select(AuditEventRow.details_json).where(AuditEventRow.action == "account.create")
        ).all()
    assert methods == ['{"method":"jellyfin_import"}'] * 5


def test_import_skips_disabled_and_unknown_users(accounts, boundary, clock):
    boundary.state = "changed"
    missing = "f" * 32
    results = _import(accounts, boundary, clock, frozenset({IDS["Drew"], missing}))

    assert {r.jellyfin_user_id: r.outcome for r in results} == {
        IDS["Drew"]: ImportOutcome.UPSTREAM_DISABLED,
        missing: ImportOutcome.NOT_FOUND,
    }
    assert _count(accounts) == 0


def test_first_sign_in_creates_one_account_and_later_sign_ins_reuse_it(accounts, boundary, clock):
    first = _sign_in(accounts, boundary, clock, "Casey")
    again = _sign_in(accounts, boundary, clock, "casey")

    assert first == again == _linked(accounts, "Casey")
    assert _row(accounts, UserRow, first).role == "user"
    with accounts.factory() as db:
        assert (
            db.scalar(
                select(AuditEventRow.actor_kind).where(AuditEventRow.action == "account.create")
            )
            == "anonymous"
        )


def test_overlapping_sign_ins_create_one_identity(accounts, boundary, clock):
    found, errors = [], []

    def attempt():
        try:
            found.append(_sign_in(accounts, boundary, clock, "Avery"))
        except Exception as error:  # surfaced below
            errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # A sign-in whose read a later one overtook is refused and can retry (fix round 2 ruling).
    assert {getattr(error, "refusal", error) for error in errors} <= {
        SignInRefusal.JELLYFIN_UNAVAILABLE
    }
    assert len(found) >= 1 and len(set(found)) == 1
    assert _count(accounts) == 1


@pytest.mark.parametrize(
    ("setup", "name", "password", "refusal"),
    [
        (None, "Casey", "wrong", SignInRefusal.INVALID_CREDENTIALS),
        (None, "Cas\ud800ey", "x", SignInRefusal.INVALID_CREDENTIALS),
        (None, "Casey", "pw\udfff", SignInRefusal.INVALID_CREDENTIALS),
        ("refused", "Casey", None, SignInRefusal.JELLYFIN_UNAVAILABLE),
    ],
)
def test_refused_first_sign_ins_create_nothing(
    accounts, boundary, clock, setup, name, password, refusal
):
    if setup == "refused":
        boundary.fault = "server-error"
    requests = len(boundary.requests)
    with pytest.raises(SignInRefused) as refused:
        _sign_in(accounts, boundary, clock, name, password)

    assert refused.value.refusal is refusal
    assert _count(accounts) == 0
    if "\ud800" in name or "\udfff" in (password or ""):
        assert len(boundary.requests) == requests


def test_a_jellyfin_administrator_starts_as_admin_and_keeps_the_owners_later_choice(
    accounts, boundary, clock
):
    avery = _sign_in(accounts, boundary, clock, "Avery")
    assert _row(accounts, UserRow, avery).role == "admin"

    with accounts.factory() as db:
        assert account_permissions.assign_role(db, avery, Role.USER, now=NOW)
        db.commit()
    assert _sign_in(accounts, boundary, clock, "Avery") == avery
    clock.now = NOW + timedelta(minutes=5)
    _sync(accounts, boundary, clock)

    assert _row(accounts, UserRow, avery).role == "user"


def test_jellyfin_status_changes_never_change_a_role(accounts, boundary, clock):
    _import(accounts, boundary, clock)
    avery, blake = _linked(accounts, "Avery"), _linked(accounts, "Blake")
    boundary.state = "changed"
    clock.now = NOW + timedelta(minutes=5)
    _sync(accounts, boundary, clock)

    # The fixture makes Avery an ordinary Jellyfin user, Blake an administrator, and renames Blake.
    assert (_row(accounts, UserRow, avery).role, _row(accounts, UserRow, blake).role) == (
        "admin",
        "user",
    )
    assert _row(accounts, UserProfileRow, blake).display_name == "Casey"


def _filter_state(accounts) -> tuple[list, list]:
    with accounts.factory() as db:
        enforcement = db.execute(select(UserFilterEnforcementRow.__table__)).all()
        preferences = db.execute(
            select(UserFilterPreferenceRow.__table__).where(UserFilterPreferenceRow.user_id <= 5)
        ).all()
    return enforcement, preferences


def _instance(accounts, rules, **chosen) -> None:
    with accounts.factory() as db:
        revision = filter_preferences_view(db, USER).instance_revision
        update = FilterDefaultsUpdate(
            revision=revision, selection_rules=rules, severity_defaults=severities(**chosen)
        )
        replace_filter_defaults(db, update)


@pytest.mark.parametrize("starting", ["live", "copy"])
def test_a_new_account_starts_in_the_starting_filter_mode_and_changes_no_enforcement(
    accounts, boundary, clock, starting
):
    required = rule("deny", category="language", severity="high")
    _instance(accounts, [required], swearing="high")
    with accounts.factory() as db:
        set_enforcement(
            db, USER, FilterPolicyUpdate(policy_revision=0, rules=[required["id"]]), now=NOW
        )
        db.commit()
        set_default_mode(db, starting)
    before = _filter_state(accounts)

    created = _sign_in(accounts, boundary, clock, "Casey")

    assert _filter_state(accounts) == before
    with accounts.factory() as db:
        view = filter_preferences_view(db, created)
    assert (view.mode, view.revision, view.policy_revision) == (starting, 0, 0)
    assert not view.mode_locked and view.enforced_rules == view.enforced_severities == []
    origin = "copy" if starting == "copy" else "instance"
    assert [(entry.rule.id, entry.origin) for entry in view.rules] == [(required["id"], origin)]

    _instance(accounts, [], swearing="low")
    with accounts.factory() as db:
        after = filter_preferences_view(db, created)
    kept = [entry.rule.id for entry in after.rules]
    assert kept == ([required["id"]] if starting == "copy" else [])
