# ruff: noqa: F811
"""MU-35 ownership evidence across an export move: returned old bytes, record-only
destinations, and the staging file of an interrupted write.

Ports the round 1 review probes for F2, F3, and F6 (web/test-results/mu-35-review).
Recovery runs through the startup sweep (`sweep_regenerations`), twice.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from scanner.db import AccountLinkExportMoveRow, MediaItemExportRow
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    identity,
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    publish_both,
    request_proof,
    transitions,
)
from tests.jellyfin_user_boundary import IDS
from tests.multi_user_access_support import ADMIN, USER
from tests.test_export_regeneration_convergence import job_state
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code


def _sweep(app) -> None:
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _publication(app, export_format):
    publish_both(app, USER, f"evidence-{export_format}")
    return next(row for row in publications(app, USER) if row[1] == export_format)


def _delete_while_absent(app, publication, held: Path) -> None:
    """Move the file away and DELETE the publication; MU-34 keeps a withdrawn record."""
    export_id, _format, _id, path, _content, revision = publication
    path.rename(held)
    with app.accounts.factory() as db:
        snapshot = json.loads(db.get(MediaItemExportRow, export_id).snapshot_json)
    response = app.send(
        "DELETE",
        f"/api/exports/{export_id}?revision={revision}&publication_id={snapshot['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )
    assert response.status_code == 204, response.text


def _new_path(publication) -> Path:
    _id, _format, export_id, path, _content, _revision = publication
    return path.parent.parent / IDS["Drew"] / path.name.replace(export_id, IDS["Drew"])


def _outcome(app, export_id):
    with app.accounts.factory() as db:
        return db.scalars(
            select(AccountLinkExportMoveRow.outcome).filter_by(export_id=export_id)
        ).one()


@pytest.mark.parametrize("export_format", ["edl", "mcf"])
def test_deleted_bytes_that_return_to_the_old_path_are_removed(link_app, tmp_path, export_format):
    publication = _publication(link_app, export_format)
    held = tmp_path / "held"
    _delete_while_absent(link_app, publication, held)
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert _outcome(link_app, publication[0]) == "absent"

    old_path = publication[3]
    old_path.parent.mkdir(parents=True, exist_ok=True)
    held.rename(old_path)
    _sweep(link_app)
    assert not old_path.exists()
    assert _outcome(link_app, publication[0]) == "removed"
    assert job_state(link_app, publication[0])[0] == "withdrawn"
    assert not _new_path(publication).exists()
    _sweep(link_app)
    assert transitions(link_app, USER)[-1][0] == "completed"


def test_other_bytes_at_a_settled_old_path_stay(link_app, tmp_path):
    publication = _publication(link_app, "edl")
    _delete_while_absent(link_app, publication, tmp_path / "held")
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    old_path = publication[3]
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"someone else's sidecar")
    _sweep(link_app)
    assert old_path.read_bytes() == b"someone else's sidecar"
    assert _outcome(link_app, publication[0]) == "absent"


@pytest.mark.parametrize("foreign", ["matching", "different"])
def test_a_file_at_a_record_only_destination_blocks_linking_untouched(link_app, tmp_path, foreign):
    publication = _publication(link_app, "edl")
    _delete_while_absent(link_app, publication, tmp_path / "held")
    content = publication[4] if foreign == "matching" else b"foreign-file"
    new_path = _new_path(publication)
    new_path.parent.mkdir(parents=True)
    new_path.write_bytes(content)

    proven = request_proof(link_app, ADMIN, USER, "Drew")
    assert "export_file_collision" in proven.json()["preview"]["blockers"]
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert code(response) == (409, "link_blocked")
    _sweep(link_app)
    assert new_path.read_bytes() == content
    assert identity(link_app, USER) is None


@pytest.mark.parametrize("export_format", ["edl", "mcf"])
def test_a_record_only_move_never_claims_bytes_at_its_new_path(link_app, tmp_path, export_format):
    publication = _publication(link_app, export_format)
    _delete_while_absent(link_app, publication, tmp_path / "held")
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    new_path = _new_path(publication)
    with link_app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, publication[0])
        assert row.destination_path == str(new_path)
        assert "owned_sha256" not in json.loads(row.snapshot_json)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(publication[4])
    _sweep(link_app)
    assert new_path.read_bytes() == publication[4]


CRASH = """
import os, sys
from pathlib import Path
from scanner.persistence.repository import export_identity_moves as files
from tests.account_link_crash import main

real = files._temporary

def crash(destination, content):
    if sys.argv[3] == "torn":
        files._staging(destination).write_bytes(content[: len(content) // 2])
    else:
        real(destination, content)
    os._exit(97)

files._temporary = crash
main(Path(sys.argv[1]), int(sys.argv[2]), "never")
"""


@pytest.mark.parametrize("staged", ["fsynced", "torn"])
def test_a_crash_before_the_staging_rename_leaves_no_temporary_file(link_app, staged):
    publish_both(link_app, USER, f"staging-{staged}")
    before = publications(link_app, USER)
    result = subprocess.run(
        [sys.executable, "-B", "-c", CRASH, str(link_app.accounts.path), str(USER), staged],
        capture_output=True,
    )
    assert result.returncode == 97, result.stderr.decode()
    assert len(list(link_app.media.rglob("*.editarr-link.tmp"))) == 1
    _sweep(link_app)
    assert identity(link_app, USER) is None
    assert all(path.read_bytes() == content for *_, path, content, _rev in before)
    assert list(link_app.media.rglob("*.tmp")) == []
    assert not any(link_app.media.rglob(IDS["Drew"]))
    assert transitions(link_app, USER) == [("rolled_back", "interrupted", [None, None])]
