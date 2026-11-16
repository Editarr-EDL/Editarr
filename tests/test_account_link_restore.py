# ruff: noqa: F401, F811
"""A link backup restored after the link completed keeps a copy of every export.

Link recovery removes a file only under the shared removal rule
(`export_identity_moves.removable`) and never writes export bytes. A full restore rolls
the restored `writing` transition back and keeps the new copies, and the restore report
lists the disagreement without blocking the account (ruling wave14-fresh-install-restore).
Review round 3 R3-F1, MU-35 variant.
"""

import sqlite3

import pytest

from scanner.backup.archive import export_backup
from scanner.http import account_deletion_api, users_admin_api, users_api
from scanner.persistence.rows.db_account_links import AccountLinkTransitionRow
from scanner.security.account_links import transition
from tests.account_link_support import (
    boundary,
    clock,
    link_app,
    prove_and_link,
    publications,
    publish_both,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import preview
from tests.test_account_link_recovery import _new_paths
from tests.test_account_transfer_restore import _restored, owner_writes_committed
from tests.test_backup_activation import _fixture, _restore
from tests.test_export_regeneration_ownership import sweep
from tests.test_personal_exports_http import app, review_app


def with_deletion_routes(app):
    for router in (account_deletion_api.router, users_admin_api.router, users_api.router):
        app.client.app.include_router(router)
    return app


def test_link_backup_restored_after_the_link_completed_keeps_a_copy(
    link_app, tmp_path, monkeypatch
):
    publish_both(link_app, USER, "restored")
    before = publications(link_app, USER)
    new = _new_paths(before)
    fixture = tmp_path / "portable"
    fixture.mkdir()
    _unused, target = _fixture(fixture)
    archive = tmp_path / "midlink.backup"
    original = transition.write_files

    def write_and_snapshot(*args, **kwargs):
        original(*args, **kwargs)
        export_backup(link_app.accounts.path, link_app.accounts.path.parent, archive)

    with monkeypatch.context() as patch:
        patch.setattr(transition, "write_files", write_and_snapshot)
        assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert not any(path.exists() for *_, path, _content, _revision in before)
    assert all(path.exists() for path in new)
    pairs = list(zip(before, new, strict=True))
    report = _restore(archive, target)
    assert {(item["table"], item["finding"], item["path"]) for item in report} == {
        *(
            ("account_link_export_moves", "recorded_path_missing", str(path))
            for *_, path, _content, _revision in before
        ),
        *(("account_link_export_moves", "unrecorded_copy", str(path)) for path in new),
    }
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute(
            "SELECT user_id, state FROM account_link_transitions"
        ).fetchall() == [(USER, "writing")]
        moves = database.execute("SELECT count(*) FROM account_link_export_moves").fetchone()
    assert moves == (len(before),)
    app = with_deletion_routes(link_app)
    with _restored(app, monkeypatch, target / "scanner.db") as factory:
        sweep(app)
        sweep(app)
        assert [state for state, *_ in _transitions(factory)] == ["rolled_back"]
        assert preview(app, ADMIN, USER, "retain").json()["blockers"] == []
        assert not any(path.exists() for (*_, path, _c, _r), _n in pairs)
        assert all(new_path.read_bytes() == content for (*_, content, _r), new_path in pairs)
        owner_writes_committed(factory, [export_id for export_id, *_ in before])
    for (*_, path, content, _revision), new_path in pairs:
        assert path.read_bytes() == content


def _transitions(factory):
    with factory() as db:
        return [(row.state,) for row in db.query(AccountLinkTransitionRow).order_by("id")]
