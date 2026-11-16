"""MU-16: the pure transition table for linked accounts, on a fixed clock."""

from dataclasses import replace
from datetime import timedelta

import pytest

from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason, UserDeleted
from scanner.security.access.identity import DisableCause, ProviderLinkState
from scanner.security.access.link_state import (
    VERIFICATION_GRACE,
    effective_link_state,
)
from scanner.security.access.vocabulary import Role
from scanner.security.jellyfin_accounts.states import (
    DISCONNECTED,
    Disconnected,
    LinkedAccount,
    SyncOutcome,
    UpstreamState,
    starting_role,
    transition,
)
from tests.jellyfin_user_boundary import NOW, identity
from tests.media_access_support import jellyfin_user

LAST = NOW - timedelta(hours=1)
USER = LinkedAccount(1, Role.USER, None, LAST)
ADMIN = replace(USER, role=Role.ADMIN)
OWNER = replace(USER, role=Role.OWNER)
OUTAGE = Unverified(UnverifiedReason.UNREACHABLE)
STATE = ProviderLinkState
S, OUT, U, C = ProviderLinkState, SyncOutcome, UpstreamState, DisableCause
LOCAL = replace(USER, disable_cause=C.LOCAL)
BY_JELLYFIN = replace(USER, disable_cause=C.UPSTREAM_DISABLED)
GONE = replace(USER, disable_cause=C.UPSTREAM_DELETED)
UD, GONE_CAUSE = C.UPSTREAM_DISABLED, C.UPSTREAM_DELETED
NEVER_VERIFIED = replace(USER, last_verified_at=None)
DELETED = UserDeleted(identity("Casey"), NOW)
KEY_REJECTED = Unverified(UnverifiedReason.KEY_REJECTED)
MISMATCH = Unverified(UnverifiedReason.SERVER_MISMATCH)


def _snapshot(*, admin=False, disabled=False):
    return replace(jellyfin_user("Casey"), is_administrator=admin, is_disabled=disabled)


@pytest.mark.parametrize(
    ("account", "evidence", "outcome", "state", "upstream", "cause"),
    [
        (USER, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, None),
        (USER, _snapshot(admin=True), OUT.VERIFIED, S.VERIFIED, U.ENABLED, None),
        (ADMIN, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, None),
        (OWNER, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, None),
        (USER, _snapshot(disabled=True), OUT.DISABLED, S.VERIFIED, U.DISABLED, UD),
        (OWNER, _snapshot(disabled=True), OUT.DISABLED, S.VERIFIED, U.DISABLED, None),
        (BY_JELLYFIN, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, None),
        (BY_JELLYFIN, _snapshot(disabled=True), OUT.DISABLED, S.VERIFIED, U.DISABLED, UD),
        (LOCAL, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, C.LOCAL),
        (LOCAL, _snapshot(disabled=True), OUT.DISABLED, S.VERIFIED, U.DISABLED, C.LOCAL),
        (USER, DELETED, OUT.DELETED, S.VERIFIED, U.DELETED, GONE_CAUSE),
        (BY_JELLYFIN, DELETED, OUT.DELETED, S.VERIFIED, U.DELETED, GONE_CAUSE),
        (LOCAL, DELETED, OUT.DELETED, S.VERIFIED, U.DELETED, C.LOCAL),
        (GONE, _snapshot(), OUT.VERIFIED, S.VERIFIED, U.ENABLED, GONE_CAUSE),
        (OWNER, DELETED, OUT.DELETED, S.VERIFIED, U.DELETED, None),
        (USER, OUTAGE, OUT.UNVERIFIED, S.OUTAGE_GRACE, None, None),
        (BY_JELLYFIN, OUTAGE, OUT.UNVERIFIED, S.OUTAGE_GRACE, None, UD),
        (USER, KEY_REJECTED, OUT.UNVERIFIED, S.OUTAGE_GRACE, None, None),
        (USER, MISMATCH, OUT.SERVER_REPLACED, S.SERVER_REPLACED, None, None),
        (BY_JELLYFIN, Disconnected(), OUT.SERVER_REPLACED, S.SERVER_REPLACED, None, UD),
        (NEVER_VERIFIED, OUTAGE, OUT.UNVERIFIED, S.OUTAGE_EXPIRED, None, None),
    ],
)
def test_each_outcome_has_one_transition(account, evidence, outcome, state, upstream, cause):
    change = transition(account, evidence, NOW)

    assert (change.outcome, change.link_state, change.upstream) == (outcome, state, upstream)
    assert change.disable_cause is cause
    stores_snapshot = outcome in (SyncOutcome.VERIFIED, SyncOutcome.DISABLED)
    assert (change.snapshot is evidence) is stores_snapshot


def test_jellyfin_administrators_start_as_admins_and_everyone_else_as_users():
    assert starting_role(_snapshot(admin=True)) is Role.ADMIN
    assert starting_role(_snapshot()) is Role.USER


def test_a_failure_names_its_reason_and_never_moves_verification():
    for evidence, failure in (
        (OUTAGE, "unreachable"),
        (Unverified(UnverifiedReason.SERVER_MISMATCH), "server_mismatch"),
        (Disconnected(), DISCONNECTED),
    ):
        change = transition(USER, evidence, NOW)
        assert (change.failure, change.snapshot, change.upstream) == (failure, None, None)


def test_an_outage_keeps_grace_until_exactly_24_hours_after_the_last_success():
    at_limit = replace(USER, last_verified_at=NOW - VERIFICATION_GRACE)
    past_limit = replace(
        USER, last_verified_at=NOW - VERIFICATION_GRACE - timedelta(microseconds=1)
    )

    assert transition(at_limit, OUTAGE, NOW).link_state is STATE.OUTAGE_GRACE
    assert transition(past_limit, OUTAGE, NOW).link_state is STATE.OUTAGE_EXPIRED


@pytest.mark.parametrize("stored", [STATE.VERIFIED, STATE.OUTAGE_GRACE])
def test_the_effective_state_expires_by_age_whatever_was_stored(stored):
    def state(verified, now):
        return effective_link_state(stored, verified, now, last_read_at=NOW)

    assert state(NOW, NOW + VERIFICATION_GRACE) is stored
    assert state(NOW, NOW + VERIFICATION_GRACE + timedelta(microseconds=1)) is STATE.OUTAGE_EXPIRED
    assert state(None, NOW) is STATE.OUTAGE_EXPIRED


@pytest.mark.parametrize("stored", [STATE.SERVER_REPLACED, STATE.OUTAGE_EXPIRED])
def test_blocked_states_stay_blocked(stored):
    assert effective_link_state(stored, NOW, NOW, last_read_at=None) is stored
