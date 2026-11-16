# ruff: noqa: F811
"""MU-35 rollback removes a new export only when Editarr placed that very file.

The planned digest says what Editarr meant to write, not that it wrote anything. A
file with matching bytes that appears at the new path before Editarr places its own
is someone else's and survives rollback and recovery. Editarr's file is the one it
created at the staging name, whose identity commits on the move before placement, so
a crash at any point after placement still recognizes it. The identity is the inode
alone, because a remount after the crash can renumber the device.

Ports the round 5 review probes for K1 (web/test-results/mu-35-review-5) and the
round 6 remount probe for L1 (web/test-results/mu-35-review-6).
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from scanner.db import AccountLinkExportMoveRow
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    request_proof,
    transitions,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, publish, review_app, seeded_review  # noqa: F401
from tests.users_api_http_support import code

FORMATS = ["edl", "mcf"]


def _published(app, export_format, name):
    review = seeded_review(app, USER, name)
    assert publish(app, review, export_format).status_code == 200
    return publications(app, USER)[0]


def _recover(app) -> None:
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _crash(app, point) -> Path:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.account_link_crash",
            str(app.accounts.path),
            str(USER),
            point,
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == 97, result.stderr.decode()
    with app.accounts.factory() as db:
        return Path(db.scalars(select(AccountLinkExportMoveRow)).one().new_path)


def _recover_after_remount(app, monkeypatch, path: Path, field: int) -> None:
    """Recover while `path` reports a renumbered `os.stat_result` `field`, as after the
    share holding it is remounted."""
    real = os.lstat

    def lstat(target, *args, **kwargs):
        status = real(target, *args, **kwargs)
        if Path(target) != path:
            return status
        fields = list(status)
        fields[field] += 2
        return os.stat_result(fields)

    with monkeypatch.context() as patched:
        patched.setattr(files.os, "lstat", lstat)
        _recover(app)


DEVICE, INODE = 2, 1


def _assert_rolled_back(app, old) -> None:
    assert identity(app, USER) is None
    assert publications(app, USER) == [old]
    assert old[3].read_bytes() == old[4]
    assert transitions(app, USER)[-1][:2] == ("rolled_back", "interrupted")
    assert list(app.media.rglob("*.tmp")) == []


@pytest.mark.parametrize("export_format", FORMATS)
@pytest.mark.parametrize("matching", [True, False])
def test_a_file_placed_before_the_write_is_refused_and_kept(
    link_app, monkeypatch, export_format, matching
):
    old = _published(link_app, export_format, "late-collision")
    proof = request_proof(link_app, ADMIN, USER, "Drew").json()
    real, planted = files.write_new, []

    def place_before_write(move, new_id):
        content = move.content if matching else b"foreign independent bytes"
        move.new_path.parent.mkdir(parents=True, exist_ok=True)
        move.new_path.write_bytes(content)
        planted.append((move.new_path, content))
        return real(move, new_id)

    monkeypatch.setattr(files, "write_new", place_before_write)
    response = confirm(link_app, ADMIN, USER, proof["proof"], proof["confirmation"])
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "export_file_collision"
    assert identity(link_app, USER) is None
    assert old[3].read_bytes() == old[4]
    assert transitions(link_app, USER)[-1][:2] == ("rolled_back", "write_failed")
    path, content = planted[0]
    assert path.read_bytes() == content


@pytest.mark.parametrize("export_format", FORMATS)
@pytest.mark.parametrize("matching", [True, False])
def test_a_file_placed_while_the_staged_file_waits_is_refused_and_kept(
    link_app, monkeypatch, export_format, matching
):
    old = _published(link_app, export_format, "staged-collision")
    real, planted = files.place_new, []

    def place_foreign_first(new_path):
        content = old[4] if matching else b"foreign independent bytes"
        new_path.write_bytes(content)
        planted.append((new_path, content))
        return real(new_path)

    monkeypatch.setattr(files, "place_new", place_foreign_first)
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "export_file_collision"
    assert identity(link_app, USER) is None
    path, content = planted[0]
    assert path.read_bytes() == content
    assert list(link_app.media.rglob("*.tmp")) == []


@pytest.mark.parametrize("export_format", FORMATS)
@pytest.mark.parametrize("point", ["after_begin", "after_staging", "after_record"])
@pytest.mark.parametrize("matching", [True, False])
def test_recovery_keeps_a_foreign_file_where_nothing_was_placed(
    link_app, export_format, point, matching
):
    old = _published(link_app, export_format, "unplaced-crash")
    path = _crash(link_app, point)
    assert not path.exists()
    content = old[4] if matching else b"unrelated bytes"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _recover(link_app)
    _assert_rolled_back(link_app, old)
    assert path.read_bytes() == content


@pytest.mark.parametrize("export_format", FORMATS)
@pytest.mark.parametrize("point", ["after_place", "mid_write", "after_write"])
def test_recovery_removes_the_file_editarr_placed(link_app, export_format, point):
    old = _published(link_app, export_format, "placed-crash")
    path = _crash(link_app, point)
    assert path.read_bytes() == old[4]
    _recover(link_app)
    _assert_rolled_back(link_app, old)
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("export_format", FORMATS)
@pytest.mark.parametrize("point", ["after_place", "mid_write", "after_write"])
def test_recovery_after_a_remount_removes_the_file_editarr_placed(
    link_app, monkeypatch, export_format, point
):
    old = _published(link_app, export_format, "remount-crash")
    path = _crash(link_app, point)
    _recover_after_remount(link_app, monkeypatch, path, DEVICE)
    _assert_rolled_back(link_app, old)
    assert not path.exists()
    retry = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert retry.status_code == 200, retry.text
    assert retry.json()["state"] == "completed"
    assert publications(link_app, USER)[0][3].read_bytes() == old[4]


@pytest.mark.parametrize("export_format", FORMATS)
def test_a_renumbered_inode_keeps_the_file_until_the_operator_removes_it(
    link_app, monkeypatch, export_format
):
    old = _published(link_app, export_format, "renumbered-crash")
    path = _crash(link_app, "after_write")
    _recover_after_remount(link_app, monkeypatch, path, INODE)
    _assert_rolled_back(link_app, old)
    assert path.read_bytes() == old[4]
    proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    assert proven["preview"]["blockers"] == ["export_file_collision"]
    blocked = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(blocked) == (409, "link_blocked")
    assert path.read_bytes() == old[4]
    path.unlink()
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200


@pytest.mark.parametrize("export_format", FORMATS)
def test_recovery_keeps_a_copy_that_replaced_the_placed_file(link_app, export_format):
    old = _published(link_app, export_format, "replaced-crash")
    path = _crash(link_app, "after_write")
    copy = path.with_name("copy")
    copy.write_bytes(path.read_bytes())
    copy.replace(path)
    _recover(link_app)
    _assert_rolled_back(link_app, old)
    assert path.read_bytes() == old[4]


@pytest.mark.parametrize("export_format", FORMATS)
def test_a_share_without_hard_links_still_links(link_app, monkeypatch, export_format):
    old = _published(link_app, export_format, "no-hard-links")

    def unsupported(*_args, **_kwargs):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(files.os, "link", unsupported)
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    moved = publications(link_app, USER)[0]
    assert moved[3].read_bytes() == old[4]
    assert not old[3].exists()
    assert list(link_app.media.rglob("*.tmp")) == []
