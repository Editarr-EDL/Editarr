"""Local connection lifecycle preserves missing-key recovery and its contributor."""

import pytest

from scanner.security import community_identity as store
from scanner.security.community_key_recovery import bind_recovery, recovery_source
from tests.multi_user_access_support import ADMIN, USER
from tests.test_community_identity_http import _home
from tests.test_community_identity_http import accounts as accounts
from tests.test_community_identity_http import app as app
from tests.test_community_identity_http import service as service
from tests.users_api_http_support import code


@pytest.mark.parametrize("actor", [USER, ADMIN], ids=["own", "named-admin"])
@pytest.mark.parametrize("bound", [False, True], ids=["started", "bound-intent"])
def test_pending_missing_key_recovery_survives_disconnect_and_reconnect(app, service, actor, bound):
    started = app.ok("POST", "/api/community/enrollment/start", USER)
    service.authorize(started["authorizationUrl"])
    app.ok(
        "POST",
        "/api/community/enrollment/complete",
        USER,
        {"state_id": "a" * 64, "confirmed": True},
    )
    home = _home(app, USER)
    original = store.load_or_create_installation_identity(home)
    (home.root / original.key_file).unlink()
    recovery = app.ok(
        "POST",
        "/api/community/keys/recovery/start",
        USER,
        {"old_key_id": original.key_id, "confirmed": True},
    )
    state_id = "b" * 64
    if bound:
        bind_recovery(home, state_id)
    pending = recovery_source(home)
    assert pending.private_key is None and pending.recovery["state"] == "pending"
    service.expect_recovery(pending, state_id)
    before = {entry.name: entry.read_bytes() for entry in home.root.iterdir() if entry.is_file()}
    requests = list(service.requests)
    connection = (
        "/api/community/connection" if actor == USER else f"/api/users/{USER}/community/connection"
    )

    for _attempt in range(2):
        detached = app.ok("DELETE", connection, actor)
        assert detached["connected"] is False and detached["pendingRecovery"] is True
    for path in (connection, connection + "/status"):
        status = app.ok("GET", path, actor)
        assert status["connected"] is False and status["pendingRecovery"] is True
        assert status["profile"]["contributorId"] == original.contributor_id
    assert code(
        app.send(
            "POST",
            "/api/community/keys/recovery/complete",
            USER,
            {"state_id": state_id, "confirmed": True},
        )
    ) == (409, "not_connected")

    enrollment = connection.removesuffix("connection") + "enrollment/start"
    reconnected = app.ok("POST", enrollment, actor)
    assert reconnected["connected"] is True and reconnected["pendingRecovery"] is True
    assert reconnected["profile"]["contributorId"] == original.contributor_id
    assert "authorizationUrl" not in reconnected
    status = app.ok("GET", connection + "/status", actor)
    assert status["connected"] is True and status["pendingRecovery"] is True
    assert code(app.send("GET", connection, actor)) == (409, "identity_incomplete")
    resumed = app.ok(
        "POST",
        "/api/community/keys/recovery/start",
        USER,
        {"old_key_id": original.key_id, "confirmed": True},
    )
    assert resumed["authorizationUrl"] == recovery["authorizationUrl"]
    assert resumed["pending"]["operationId"] == pending.recovery["operationId"]
    assert {entry.name: entry.read_bytes() for entry in home.root.iterdir() if entry.is_file()} == (
        before
    )
    assert service.requests == requests

    completed = app.ok(
        "POST",
        "/api/community/keys/recovery/complete",
        USER,
        {"state_id": state_id, "confirmed": True},
    )
    assert completed["completed"]["operationId"] == pending.recovery["operationId"]
    assert completed["identity"]["contributorId"] == original.contributor_id
    assert completed["identity"]["keyId"] == pending.recovery["newKeyId"]
    active = app.ok("GET", connection, actor)
    assert active["connected"] is True and active["pendingRecovery"] is False
    assert active["profile"]["contributorId"] == original.contributor_id
    assert len(service.contributors) == 1
    assert all(
        path != "/v1/enrollments/complete" for path, _, _ in service.requests[len(requests) :]
    )
    assert _home(app, ADMIN) is None
