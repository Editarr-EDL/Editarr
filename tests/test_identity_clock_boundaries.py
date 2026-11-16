"""MU-43 clock boundaries over HTTP: owner immunity, the 24-hour outage limit,
five-minute scheduled sync, and fresh-authentication refusals.

Reuses the MU-16 HTTP fixture (`mu04-access-builders-3` accounts over the MU-08
`JellyfinBoundary`, `editarr-jellyfin-users-1`, a synthetic local server, not
live Jellyfin). Sessions, link expiry, and the scheduler share one fixed clock.
"""

from __future__ import annotations

from datetime import timedelta

import scanner.http.jellyfin_accounts_api as jellyfin_accounts_api
from scanner.db import UserProviderIdentityRow
from scanner.runtime.jellyfin_sync_scheduler import JellyfinSyncScheduler
from scanner.security.auth_repository import issue_session
from tests.jellyfin_user_boundary import IDS
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.test_jellyfin_accounts_http import _get, _key, _linked, _session, _sign_in, _sync
from tests.test_jellyfin_accounts_http import app as app
from tests.test_jellyfin_accounts_http import boundary as boundary
from tests.test_jellyfin_accounts_http import clock as clock
from tests.users_api_http_support import code, fresh

OUTAGE_LIMIT = timedelta(hours=24)


def _link_owner(app, boundary, clock) -> None:
    with app.accounts.factory() as db:
        owner = db.get(UserProviderIdentityRow, OWNER)
        owner.server_id, owner.provider_user_id = boundary.server_info()["Id"], IDS["Drew"]
        owner.last_verified_at = clock.now.replace(tzinfo=None)
        db.commit()


def _renew_session(app, clock, user_id: int) -> None:
    """Replace the fixture's session, which the advanced clock has expired, with a new one."""
    with app.accounts.factory() as db:
        app.accounts.sessions[user_id] = issue_session(
            db,
            remember_me=False,
            user_id=user_id,
            now=clock.now,
            configuration=app.accounts.configuration,
        )


def test_past_24_hours_of_outage_a_member_drops_to_profile_but_the_owner_keeps_everything(
    app, boundary, clock
):
    _link_owner(app, boundary, clock)
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"user_ids": [IDS["Avery"]]})
    avery = _linked(app, "Avery")
    member, owner = _key(app, avery), app.accounts.key(OWNER)
    boundary.fault = "server-error"
    clock.now += OUTAGE_LIMIT
    _sync(app)
    assert _get(app, "/api/users", member).status_code == 200

    clock.now += timedelta(seconds=1)
    _sync(app)
    assert code(_get(app, "/api/users", member)) == (403, "capability_denied")
    assert _get(app, f"/api/users/{avery}", member).status_code == 200
    assert _get(app, "/api/users", owner).status_code == 200
    _renew_session(app, clock, OWNER)
    _renew_session(app, clock, ADMIN)
    change = {"overrides": {"accounts.list": "deny"}, **fresh(OWNER)}
    # Jellyfin confirms the owner's password, so an outage refuses its fresh actions.
    refused = app.send("PATCH", f"/api/users/{USER}/permissions", OWNER, change)
    assert code(refused) == (403, "reauthentication_failed")
    demote = app.send("PUT", f"/api/users/{OWNER}/role", ADMIN, {"role": "user", **fresh(ADMIN)})
    assert demote.status_code == 403
    assert app.ok("GET", f"/api/users/{OWNER}", OWNER)["role"] == "owner"


def test_the_scheduler_applies_an_upstream_disable_at_five_minutes_not_before(app, boundary, clock):
    app.ok("POST", "/api/jellyfin/users/import", ADMIN, {"user_ids": [IDS["Drew"]]})
    drew = _linked(app, "Drew")
    key, profile = _key(app, drew), f"/api/users/{drew}"
    scheduler = JellyfinSyncScheduler(
        app.accounts.factory, clock=clock, sync=jellyfin_accounts_api.registry
    )
    start = clock.now
    assert scheduler.tick() is not None
    boundary.state = "changed"
    assert _get(app, profile, key).status_code == 200

    clock.now = start + timedelta(minutes=4, seconds=59)
    assert scheduler.tick() is None
    assert _get(app, profile, key).status_code == 200

    clock.now = start + timedelta(minutes=5)
    assert scheduler.tick() is not None
    assert code(_get(app, profile, key)) == (403, "account_disabled")


def test_fresh_authentication_needs_a_live_interactive_session_never_a_key(app, clock):
    proof = fresh(OWNER)

    by_key = app.send("POST", "/api/auth/api-key/reveal", OWNER, proof, key=True)
    assert code(by_key) == (403, "interactive_session_required")
    transfer = app.send("POST", f"/api/users/{ADMIN}/ownership", OWNER, proof, key=True)
    assert code(transfer) == (403, "interactive_session_required")
    assert app.ok("GET", f"/api/users/{OWNER}", OWNER)["role"] == "owner"

    casey = _session(_sign_in(app, "Casey"))
    clock.now += timedelta(days=400)
    app.client.cookies.clear()
    headers, csrf = casey
    expired = app.client.post(
        "/api/auth/api-key",
        json={"reauthentication": {"password": "jf-casey-password"}},
        headers={**headers, "X-CSRF-Token": csrf},
    )
    assert expired.status_code == 401
