# ruff: noqa: F401, F811
"""Transfer and link recovery are fail-safe (rulings wave13-mu38-failsafe-recovery and
wave13-mu38-defer-restore).

Recovery never writes export bytes; reconciling settled moves acts only on owned bytes
at the old path and accepts the publication's current recorded path as the surviving
copy; a link waits for the account's open transfer moves. Review round 4 R4-A to R4-C.
"""

import errno
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow
from scanner.persistence.repository import export_identity_moves
from scanner.persistence.rows.db_account_dispositions import AccountTransferMoveRow
from scanner.persistence.rows.db_account_links import AccountLinkExportMoveRow
from scanner.security.account_links import transition
from tests.account_link_support import (
    REPLACED_CASEY,
    boundary,
    clock,
    confirm,
    link_app,
    prove_and_link,
    publications,
    publish_both,
    request_proof,
    sync,
    transitions,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import preview
from tests.test_account_link_restore import with_deletion_routes
from tests.test_account_transfer_recovery import TRANSFER_AND_EXIT
from tests.test_export_regeneration_ownership import sweep
from tests.test_personal_exports_http import app, review_app


def _link_while_old_files_vanish(app, monkeypatch, name):
    """Link while the old files disappear after the new ones are written: moves settle absent."""
    before = publications(app, USER)
    original = transition.write_files

    def write_then_lose_old(*args, **kwargs):
        original(*args, **kwargs)
        for *_rest, path, _content, _revision in before:
            path.unlink()

    with monkeypatch.context() as patch:
        patch.setattr(transition, "write_files", write_then_lose_old)
        assert prove_and_link(app, ADMIN, USER, name).status_code == 200
    assert transitions(app, USER)[-1][2] == ["absent", "absent"]
    return before


def _media(app, export_id) -> Path:
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        path = Path(row.destination_path)
        return path.parents[2] / Path(json.loads(row.snapshot_json)["media_path"]).name


def test_the_sweep_never_recreates_a_removed_sidecar_of_a_settled_move(link_app, monkeypatch):
    publish_both(link_app, USER, "settled")
    _link_while_old_files_vanish(link_app, monkeypatch, "Drew")
    export_id, *_unused, new_path, _content, _revision = publications(link_app, USER)[0]
    new_path.unlink()
    _media(link_app, export_id).unlink()
    sweep(link_app)
    sweep(link_app)
    assert not new_path.exists()


def test_returned_old_bytes_go_once_a_later_link_moved_the_publication_on(link_app, monkeypatch):
    publish_both(link_app, USER, "twice")
    before = _link_while_old_files_vanish(link_app, monkeypatch, "Casey")
    link_app.boundary.state = "replaced"
    sync(link_app)
    proven = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY).json()
    assert (
        confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"]).status_code == 200
    )
    for *_rest, old_path, content, _revision in before:
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_bytes(content)
    sweep(link_app)
    assert not any(path.exists() for *_rest, path, _content, _revision in before)
    with link_app.accounts.factory() as db:
        outcomes = [row.outcome for row in db.query(AccountLinkExportMoveRow).order_by("id")]
    assert outcomes == ["removed"] * 4
    after = publications(link_app, USER)
    assert all(path.read_bytes() == content for *_rest, path, content, _r in after)


def _crash_transfer(app):
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    arguments = [str(app.accounts.engine.url), str(USER), str(ADMIN), "written"]
    result = subprocess.run(
        [sys.executable, "-B", "-c", TRANSFER_AND_EXIT, *arguments, body["confirmation"]],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 75, result.stderr.decode()
    with app.accounts.factory() as db:
        return [Path(row.new_path) for row in db.query(AccountTransferMoveRow)]


def _open_transfer_moves(app) -> int:
    with app.accounts.factory() as db:
        return db.query(AccountTransferMoveRow).filter_by(outcome=None).count()


def test_a_link_first_settles_an_interrupted_transfer(link_app):
    app = with_deletion_routes(link_app)
    publish_both(app, USER, "stranded")
    placed = _crash_transfer(app)
    assert all(path.exists() for path in placed)
    assert prove_and_link(app, ADMIN, USER, "Drew").status_code == 200
    sweep(app)
    assert _open_transfer_moves(app) == 0
    assert not any(path.exists() for path in placed)
    assert preview(app, ADMIN, USER, "retain").json()["blockers"] == []
    assert all(path.read_bytes() == content for *_r, path, content, _v in publications(app, USER))


def test_a_link_waits_for_transfer_moves_recovery_cannot_settle(link_app, monkeypatch):
    app = with_deletion_routes(link_app)
    publish_both(app, USER, "unsettled")
    placed = _crash_transfer(app)

    def unreadable(*_args):
        raise OSError(errno.EIO, "Input/output error")

    with monkeypatch.context() as patch:
        patch.setattr(export_identity_moves, "serves", unreadable)
        proven = request_proof(app, ADMIN, USER, "Drew")
        assert "account_transfer_unsettled" in proven.json()["preview"]["blockers"]
        body = proven.json()
        refused = confirm(app, ADMIN, USER, body["proof"], body["confirmation"])
    assert refused.status_code == 409 and refused.json()["code"] == "link_blocked"
    assert all(path.exists() for path in placed) and _open_transfer_moves(app) == 2
    assert prove_and_link(app, ADMIN, USER, "Drew").status_code == 200
    assert _open_transfer_moves(app) == 0 and not any(path.exists() for path in placed)


def test_a_refused_link_with_a_hand_edited_sidecar_leaves_no_block(link_app, monkeypatch):
    """R6-A: a link whose new files could not be written changes nothing, even when the
    user had edited a sidecar, so nothing is left to block the account."""
    app = with_deletion_routes(link_app)
    publish_both(app, USER, "refused")
    edited = publications(app, USER)[0][3]
    edited.write_bytes(edited.read_bytes() + b"\n* user note\n")

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(transition, "write_files", no_space)
        assert prove_and_link(app, ADMIN, USER, "Drew").status_code == 409
    sweep(app)
    assert transitions(app, USER)[-1][0] == "rolled_back"
    assert preview(app, ADMIN, USER, "retain").json()["blockers"] == []
    assert edited.read_bytes().endswith(b"* user note\n")


@pytest.mark.parametrize("change", ["edited", "deleted"])
def test_a_users_own_sidecar_change_after_a_link_never_blocks(link_app, monkeypatch, change):
    """R6-B: editing or deleting one's own sidecar after a link is not a recovery state."""
    app = with_deletion_routes(link_app)
    publish_both(app, USER, "hand-changed")
    _link_while_old_files_vanish(app, monkeypatch, "Drew")
    for *_rest, path, _content, _revision in publications(app, USER):
        if change == "edited":
            path.write_bytes(path.read_bytes() + b"\n* user note\n")
        else:
            path.unlink()
    sweep(app)
    assert transitions(app, USER)[-1] == ("completed", None, ["absent", "absent"])
    assert preview(app, ADMIN, USER, "retain").json()["blockers"] == []
