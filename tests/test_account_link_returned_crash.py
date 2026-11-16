# ruff: noqa: F811
"""MU-35 crash safety for returned old bytes of a withdrawn record.

Removing those bytes destroys the only evidence that the media is back, so the requeue
they earn must survive every interruption of the removal: a hard exit at each cleanup
boundary (`tests.account_link_returned_crash`) and a directory fsync error after the
unlink. Covers both cleanup paths, `finish` of a `removing` transition and
reconciliation of a `completed` one, in both formats.

Ports the round 3 review probes for H1 (web/test-results/mu-35-review-3).
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys

import pytest

from scanner.persistence.repository import export_identity_moves as files
from scanner.security.account_links import transition
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_link_withdrawn import (
    _outcome,
    _serves_under_drew,
    _settle,
    _sweep,
    _withdrawn_during_outage,
)
from tests.test_export_regeneration_convergence import job_state
from tests.test_personal_exports_http import app, review_app  # noqa: F401

POINTS = ["before_intent", "after_intent", "after_unlink", "before_commit", "after_commit"]


def _linked_during_outage(app, monkeypatch, export_format, cleanup):
    """Withdraw a publication during an outage, link, and leave `cleanup` to the sweep."""
    export_id, content, old_path, remount = _withdrawn_during_outage(app, export_format)
    with monkeypatch.context() as patch:
        if cleanup == "finish":
            patch.setattr(transition, "finish", lambda *args: False)
        response = prove_and_link(app, ADMIN, USER, "Drew")
    expected = "removing" if cleanup == "finish" else "completed"
    assert response.status_code == 200 and response.json()["state"] == expected
    assert job_state(app, export_id)[0] == "withdrawn"
    remount()
    assert old_path.read_bytes() == content
    return export_id, old_path


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("cleanup", ["finish", "reconcile"])
@pytest.mark.parametrize("export_format", ["edl", "mcf"])
def test_a_hard_exit_while_removing_returned_bytes_still_serves_again(
    link_app, monkeypatch, export_format, cleanup, point
):
    export_id, old_path = _linked_during_outage(link_app, monkeypatch, export_format, cleanup)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.account_link_returned_crash",
            str(link_app.accounts.path),
            point,
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == 97, result.stderr.decode()
    _sweep(link_app)
    assert _settle(link_app) == ["succeeded"]
    _sweep(link_app)
    assert _settle(link_app) == []
    _serves_under_drew(link_app, export_id, old_path)
    assert _outcome(link_app, export_id) in {"removed", "absent"}


@pytest.mark.parametrize("cleanup", ["finish", "reconcile"])
@pytest.mark.parametrize("export_format", ["edl", "mcf"])
def test_a_directory_fsync_error_after_the_unlink_keeps_the_requeue(
    link_app, monkeypatch, export_format, cleanup
):
    export_id, old_path = _linked_during_outage(link_app, monkeypatch, export_format, cleanup)
    real_sync = files.sync_directory

    def failing_sync(directory):
        if directory == old_path.parent and not old_path.exists():
            raise OSError(errno.EIO, "directory fsync failed after the unlink")
        real_sync(directory)

    with monkeypatch.context() as patch:
        patch.setattr(files, "sync_directory", failing_sync)
        _sweep(link_app)
    assert job_state(link_app, export_id)[0] == "queued"
    _sweep(link_app)
    assert _settle(link_app) == ["succeeded"]
    _serves_under_drew(link_app, export_id, old_path)
