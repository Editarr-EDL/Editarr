# ruff: noqa: F811
"""MU-35 cleanup never follows a symbolic link out of the export layout.

Old-path removal at link time and in reconciliation, rollback of new files, and staging
cleanup all apply `safe_destination`'s rule (MU-31) to the whole path below the media
directory. Bytes reached through a link are not Editarr's, even when they match, so
they stay and the move records `kept`.

Ports the round 3 review probes for H3 (web/test-results/mu-35-review-3).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from scanner.persistence.repository.export_regeneration import sweep_regenerations
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    publish_both,
    transitions,
)
from tests.jellyfin_user_boundary import IDS
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_link_withdrawn import _outcome
from tests.test_personal_exports_http import app, review_app  # noqa: F401

FORMATS = ["edl", "mcf"]
FOREIGN = b"not an Editarr publication"


def _sweep(app) -> None:
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _publication(app, export_format, name):
    publish_both(app, USER, name)
    return next(row for row in publications(app, USER) if row[1] == export_format)


def _link_directory(entry, outside, name, content):
    """Make `entry` a link to a new directory `outside` holding `name` with `content`."""
    outside.mkdir(parents=True)
    (outside / name).write_bytes(content)
    entry.symlink_to(outside, target_is_directory=True)
    return outside / name


@pytest.mark.parametrize("matching", [True, False])
@pytest.mark.parametrize("entry", ["id_directory", "file"])
@pytest.mark.parametrize("export_format", FORMATS)
def test_linking_keeps_bytes_reached_through_a_link_at_the_old_path(
    link_app, tmp_path, export_format, entry, matching
):
    export_id, _f, _id, old_path, content, _r = _publication(link_app, export_format, "old-link")
    foreign_bytes = content if matching else FOREIGN
    if entry == "id_directory":
        old_path.parent.rename(old_path.parent.with_name("held-old-id"))
        external = _link_directory(old_path.parent, tmp_path / "out", old_path.name, foreign_bytes)
    else:
        old_path.rename(old_path.with_name("held-old-file"))
        external = tmp_path / old_path.name
        external.write_bytes(foreign_bytes)
        old_path.symlink_to(external)

    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    assert external.read_bytes() == foreign_bytes
    assert old_path.is_symlink() or old_path.parent.is_symlink()
    assert _outcome(link_app, export_id) == "kept"


@pytest.mark.parametrize("entry", ["id_directory", "format_directory"])
@pytest.mark.parametrize("export_format", FORMATS)
def test_reconciliation_keeps_matching_bytes_behind_a_link_above_the_old_path(
    link_app, tmp_path, export_format, entry
):
    export_id, _f, _id, old_path, content, _r = _publication(link_app, export_format, "late-link")
    old_path.parent.rename(old_path.parent.with_name("unmounted-old-id"))
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert _outcome(link_app, export_id) == "absent"

    if entry == "id_directory":
        external = _link_directory(old_path.parent, tmp_path / "out", old_path.name, content)
    else:
        format_directory = old_path.parent.parent
        format_directory.rename(format_directory.with_name("held-format"))
        external = tmp_path / "out" / old_path.parent.name / old_path.name
        external.parent.mkdir(parents=True)
        external.write_bytes(content)
        format_directory.symlink_to(tmp_path / "out", target_is_directory=True)
    _sweep(link_app)
    assert external.read_bytes() == content
    assert external.parent.is_dir()
    assert _outcome(link_app, export_id) == "absent"


def test_rollback_keeps_new_and_staging_bytes_behind_a_link(link_app, tmp_path):
    publish_both(link_app, USER, "rollback-link")
    before = publications(link_app, USER)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.account_link_crash",
            str(link_app.accounts.path),
            str(USER),
            "after_staging",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == 97, result.stderr.decode()
    staged = next(link_app.media.rglob("*.editarr-link.tmp"))
    new_directory = staged.parent
    assert new_directory.name == IDS["Drew"]
    outside = tmp_path / "outside"
    new_directory.rename(outside)
    new_name = next(
        path.name.replace(export_id, IDS["Drew"])
        for _id, fmt, export_id, path, _c, _r in before
        if path.parent.parent == new_directory.parent
    )
    content = next(c for *_, path, c, _r in before if path.parent.parent == new_directory.parent)
    (outside / new_name).write_bytes(content)
    new_directory.symlink_to(outside, target_is_directory=True)

    _sweep(link_app)
    assert transitions(link_app, USER)[0][:2] == ("rolled_back", "interrupted")
    assert (outside / staged.name).exists()
    assert (outside / new_name).read_bytes() == content
    for *_, path, old_content, _revision in before:
        assert path.read_bytes() == old_content
