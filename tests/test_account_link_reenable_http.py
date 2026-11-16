# ruff: noqa: F811
"""JF-06: linking enables an account that 0030 disabled for having no Jellyfin link.

The account keeps its reviews and exports. A disable an admin chose stays after
linking. See `tests/account_link_support.py` for the accounts, exports, and synthetic
Jellyfin.
"""

from __future__ import annotations

import scanner.http.jellyfin_login_api as jellyfin_login_api
from scanner.db import MediaItemExportRow, SavedReviewRow, UserRow
from scanner.http.auth_rate_limit import LoginRateLimiter
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    jellyfin,
    link_app,  # noqa: F401
    publications,
    publish_both,
    request_proof,
)
from tests.jellyfin_user_boundary import IDS, PRIMARY
from tests.multi_user_access_support import ORIGIN, OWNER, USER
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code


def _disable(app, cause: str) -> int:
    """Disable USER for `cause` as the migration or an admin would; return its revision."""
    with app.accounts.factory() as db:
        user = db.get(UserRow, USER)
        user.status, user.disable_cause = "disabled", cause
        user.permission_revision += 1
        db.commit()
        return user.permission_revision


def _account(app) -> tuple:
    with app.accounts.factory() as db:
        user = db.get(UserRow, USER)
        return user.status, user.disable_cause, user.permission_revision


def _owners(app, review_id: str) -> tuple:
    with app.accounts.factory() as db:
        review = db.get(SavedReviewRow, review_id)
        exports = {
            row.target_user_id
            for row in db.query(MediaItemExportRow).filter_by(review_id=review_id)
        }
        return review.owner_id, review.original_author_id, exports


def _link(app):
    proven = request_proof(app, OWNER, USER, "Drew")
    assert proven.status_code == 200, proven.text
    body = proven.json()
    return body["preview"], confirm(app, OWNER, USER, body["proof"], body["confirmation"])


def _sign_in(app, monkeypatch):
    monkeypatch.setattr(jellyfin_login_api, "jellyfin_login_limiter", LoginRateLimiter())
    app.client.app.include_router(jellyfin_login_api.router)
    app.client.cookies.clear()
    return app.client.post("/api/auth/jellyfin/login", json=jellyfin("Drew"), headers=ORIGIN)


def test_linking_enables_an_unlinked_account_that_keeps_its_reviews_and_exports(
    link_app, monkeypatch
):
    review = publish_both(link_app, USER, "kept")
    revision = _disable(link_app, "unlinked")

    preview, linked = _link(link_app)

    assert preview["status"] == {"current": "disabled", "after": "active", "cause": "unlinked"}
    assert preview["blockers"] == []
    assert linked.status_code == 200, linked.text
    status, cause, after = _account(link_app)
    assert (status, cause) == ("active", None) and after > revision
    assert identity(link_app, USER) == (PRIMARY, IDS["Drew"], "verified")
    assert _owners(link_app, review.id) == (USER, USER, {USER})
    assert {export_id for _, _, export_id, *_ in publications(link_app, USER)} == {IDS["Drew"]}
    signed_in = _sign_in(link_app, monkeypatch)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["access"]["principal"]["user_id"] == USER


def test_a_disable_an_admin_chose_survives_linking(link_app, monkeypatch):
    review = publish_both(link_app, USER, "kept-disabled")
    _disable(link_app, "local")

    preview, linked = _link(link_app)

    assert preview["status"] == {"current": "disabled", "after": "disabled", "cause": "local"}
    assert linked.status_code == 200, linked.text
    assert _account(link_app)[:2] == ("disabled", "local")
    assert identity(link_app, USER) == (PRIMARY, IDS["Drew"], "verified")
    assert _owners(link_app, review.id) == (USER, USER, {USER})
    assert code(_sign_in(link_app, monkeypatch)) == (403, "account_disabled")
