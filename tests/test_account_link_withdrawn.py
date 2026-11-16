# ruff: noqa: F811
"""MU-35 with MU-34's returned-bytes rule: a publication withdrawn while its share was
unmounted serves again under the new export ID once the share returns, even when a
link moved its record during the outage.

Ports the round 2 review probes for G1 (web/test-results/mu-35-review-2).
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from sqlalchemy import func, select

from scanner.db import AccountLinkExportMoveRow, AuditEventRow
from scanner.persistence.repository.export_regeneration import (
    retry_regeneration,
    sweep_regenerations,
)
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
    transitions,
)
from tests.jellyfin_user_boundary import IDS
from tests.multi_user_access_support import ADMIN, USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_convergence import job_state, settle_due
from tests.test_export_regeneration_failures import bump_policy
from tests.test_export_regeneration_ownership import relocate
from tests.test_personal_exports_http import app, publish, review_app, seeded_review  # noqa: F401

FORMATS = ["edl", "mcf"]


def _sweep(app) -> None:
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _settle(app) -> list[str]:
    outcomes = []
    for _ in range(6):
        outcome = settle_due(app, 1)
        if outcome is None:
            break
        outcomes.append(outcome)
    return outcomes


def _withdrawn_during_outage(app, export_format):
    """Publish on share/, unmount share, and let regeneration withdraw it as absent."""
    review = seeded_review(app, USER, f"outage-{export_format}")
    relocate(app, review, Path("share"))
    export_id = publish(app, review, export_format).json()["id"]
    _status, content, old_path = observe(app, export_id)
    bump_policy(app.accounts)
    share = app.media / "share"
    unmounted = share.with_name("share-unmounted")
    share.rename(unmounted)
    assert settle_due(app, 0) == "failed"
    assert job_state(app, export_id)[0] == "withdrawn"
    return export_id, content, old_path, lambda: unmounted.rename(share)


def _outcome(app, export_id):
    with app.accounts.factory() as db:
        return db.scalars(
            select(AccountLinkExportMoveRow.outcome).filter_by(export_id=export_id)
        ).one()


def _serves_under_drew(app, export_id, old_path):
    status, content, path = observe(app, export_id)
    assert status["serving"]
    assert IDS["Drew"] in path.name and path.read_bytes() == content
    assert not old_path.exists()


@pytest.mark.parametrize("export_format", FORMATS)
def test_a_withdrawn_export_moved_during_an_outage_serves_again_after_remount(
    link_app, export_format
):
    export_id, content, old_path, remount = _withdrawn_during_outage(link_app, export_format)
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert _outcome(link_app, export_id) == "absent"
    _sweep(link_app)
    assert _settle(link_app) == []

    remount()
    assert old_path.read_bytes() == content
    _sweep(link_app)
    assert not old_path.exists()
    assert _outcome(link_app, export_id) == "removed"
    assert job_state(link_app, export_id)[0] == "queued"
    assert _settle(link_app) == ["succeeded"]
    _serves_under_drew(link_app, export_id, old_path)

    served = observe(link_app, export_id)
    _sweep(link_app)
    assert _settle(link_app) == []
    assert observe(link_app, export_id) == served
    assert transitions(link_app, USER)[-1][0] == "completed"


@pytest.mark.parametrize("export_format", FORMATS)
def test_owned_bytes_back_before_the_link_requeue_the_moved_record(link_app, export_format):
    export_id, content, old_path, remount = _withdrawn_during_outage(link_app, export_format)
    remount()
    assert old_path.read_bytes() == content
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert _outcome(link_app, export_id) == "removed"
    assert job_state(link_app, export_id)[0] == "queued"
    assert _settle(link_app) == ["succeeded"]
    _serves_under_drew(link_app, export_id, old_path)


def test_foreign_bytes_at_the_old_path_stay_and_queue_nothing(link_app):
    export_id, _content, old_path, remount = _withdrawn_during_outage(link_app, "edl")
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    remount()
    old_path.write_bytes(b"someone else's sidecar")
    _sweep(link_app)
    assert old_path.read_bytes() == b"someone else's sidecar"
    assert _outcome(link_app, export_id) == "absent"
    assert job_state(link_app, export_id)[0] == "withdrawn"
    assert not observe(link_app, export_id)[0]["serving"]


@pytest.mark.parametrize("export_format", FORMATS)
def test_control_without_a_link_the_remount_alone_serves_again(link_app, export_format):
    export_id, content, old_path, remount = _withdrawn_during_outage(link_app, export_format)
    remount()
    _sweep(link_app)
    assert _settle(link_app) == ["succeeded"]
    status, served, path = observe(link_app, export_id)
    assert status["serving"] and path == old_path and path.read_bytes() == served


@pytest.mark.parametrize("export_format", FORMATS)
def test_control_an_explicit_retry_before_the_sweep_serves_under_the_new_id(
    link_app, export_format
):
    """The new identity's policy does not keep the export withdrawn. Once the sweep has
    queued the job, a retry is refused as not retryable, as in MU-34 without a link."""
    export_id, _content, old_path, remount = _withdrawn_during_outage(link_app, export_format)
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    remount()
    publication_id = observe(link_app, export_id)[0]["publication_id"]
    with link_app.accounts.factory() as db:
        assert retry_regeneration(db, export_id, publication_id)
    _sweep(link_app)
    with link_app.accounts.factory() as db:
        assert not retry_regeneration(db, export_id, publication_id)
    assert _settle(link_app) == ["succeeded"]
    _serves_under_drew(link_app, export_id, old_path)


@pytest.mark.parametrize("unremovable", [False, True])
def test_owned_old_bytes_that_cannot_be_removed_requeue_once(link_app, unremovable):
    export_id, _content, old_path, remount = _withdrawn_during_outage(link_app, "edl")
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    remount()
    media = next(path for path in old_path.parents[2].iterdir() if path.is_file())
    media.rename(media.with_name(f"{media.name}.gone"))
    if unremovable:
        old_path.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    with link_app.accounts.factory() as db:
        audits = db.scalar(select(func.count()).select_from(AuditEventRow))
    runs = []
    try:
        for _ in range(4):
            _sweep(link_app)
            runs += _settle(link_app)
    finally:
        if unremovable:
            old_path.parent.chmod(0o755)
    assert runs == ["failed"]
    with link_app.accounts.factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditEventRow)) == audits + 1
    assert job_state(link_app, export_id)[0] == "withdrawn"
    assert old_path.exists() is unremovable
    assert _outcome(link_app, export_id) == ("absent" if unremovable else "removed")

    _sweep(link_app)
    assert not old_path.exists()
    assert _outcome(link_app, export_id) == "removed"
    assert _settle(link_app) == []
