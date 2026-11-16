# ruff: noqa: F811
"""MU-35 path inspection that cannot resolve defers; it never aborts or counts as absence.

Python 3.11 raises RuntimeError, not OSError, when `Path.resolve` meets a symbolic-link
loop. `export_publication.resolve_directory` turns that into ELOOP at the one place
export paths are resolved, so cleanup, recovery, the MU-34 sweep, and the link preview
handle it like any other filesystem error that is not ENOENT.

Ports the round 4 review probes for J1 (web/test-results/mu-35-review-4).
"""

from __future__ import annotations

import errno

import pytest
from sqlalchemy import select

from scanner.db import AuditEventRow, UserFilterPreferenceRow
from scanner.persistence.repository.export_publication import resolve_directory
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    publish_both,
    request_proof,
)
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_account_link_withdrawn import _outcome, _withdrawn_during_outage
from tests.test_export_regeneration_convergence import job_state
from tests.test_personal_exports_http import app, publish, review_app, seeded_review  # noqa: F401

FORMATS = ["edl", "mcf"]


def _sweep_once(app) -> None:
    with app.accounts.factory() as db:
        sweep_regenerations(db)


def _loop(directory):
    """Move `directory` aside and leave a symbolic link to itself in its place."""
    held = directory.with_name(f"{directory.name}-held")
    directory.rename(held)
    directory.symlink_to(directory.name)
    return held


def _linked_while_unmounted(app, export_format):
    export_id, content, old_path, remount = _withdrawn_during_outage(app, export_format)
    response = prove_and_link(app, ADMIN, USER, "Drew")
    assert response.status_code == 200 and response.json()["state"] == "completed"
    assert _outcome(app, export_id) == "absent"
    remount()
    return export_id, content, old_path


def test_a_loop_resolves_as_eloop(tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    with pytest.raises(OSError) as raised:
        resolve_directory(loop)
    assert raised.value.errno == errno.ELOOP


@pytest.mark.parametrize("export_format", FORMATS)
def test_a_looped_old_media_directory_defers_without_stopping_other_accounts(
    link_app, export_format
):
    export_id, content, old_path = _linked_while_unmounted(link_app, export_format)
    share = old_path.parents[2]
    held = _loop(share)
    peer_review = seeded_review(link_app, PEER_USER, "peer-stale")
    peer = publish(link_app, peer_review, "edl", user=PEER_USER).json()["id"]
    with link_app.accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, PEER_USER).policy_revision = 7
        db.commit()

    _sweep_once(link_app)
    assert job_state(link_app, peer)[0] == "queued"
    assert (held / old_path.relative_to(share)).read_bytes() == content
    assert _outcome(link_app, export_id) == "absent"

    share.unlink()
    held.rename(share)
    _sweep_once(link_app)
    assert not old_path.exists()
    assert _outcome(link_app, export_id) == "removed"


@pytest.mark.parametrize("export_format", FORMATS)
def test_relinking_with_a_looped_old_media_directory_is_a_handled_refusal(link_app, export_format):
    export_id, content, old_path = _linked_while_unmounted(link_app, export_format)
    share = old_path.parents[2]
    held = _loop(share)
    with link_app.accounts.factory() as db:
        audits = len(db.scalars(select(AuditEventRow.id)).all())

    response = prove_and_link(link_app, ADMIN, USER, "Casey")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "link_blocked"
    with link_app.accounts.factory() as db:
        assert len(db.scalars(select(AuditEventRow.id)).all()) > audits
    assert (held / old_path.relative_to(share)).read_bytes() == content
    assert _outcome(link_app, export_id) == "absent"


@pytest.mark.parametrize("shape", ["loop", "missing"])
@pytest.mark.parametrize("export_format", FORMATS)
def test_a_served_publication_in_an_unresolvable_media_directory_previews_a_blocker(
    link_app, export_format, shape
):
    publish_both(link_app, USER, "served-loop")
    old_path = next(row for row in publications(link_app, USER) if row[1] == export_format)[3]
    share = old_path.parents[2]
    if shape == "loop":
        _loop(share)
    else:
        share.rename(share.with_name("held-media"))

    response = request_proof(link_app, ADMIN, USER, "Drew")
    assert response.status_code == 200, response.text
    assert "export_destination_unavailable" in response.json()["preview"]["blockers"]
