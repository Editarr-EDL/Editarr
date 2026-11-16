"""Two-user review deletion with explicit retain or remove choices for published files."""

import json
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from scanner.db import (
    AuditEventRow,
    MediaItemExportRow,
    SavedReviewRow,
    UserFilterPreferenceRow,
)
from scanner.http import review_deletion_api
from scanner.persistence.repository import review_disposition
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_export_regeneration_ownership import sweep
from tests.test_personal_exports_http import app as _exports_app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

exports_app = _exports_app


@pytest.fixture
def app(exports_app):
    exports_app.client.app.include_router(review_deletion_api.router)
    return exports_app


def published(app):
    """USER's review with USER's personal EDL/MCF, ADMIN's personal EDL, and the item default."""
    review = seeded_review(app, USER, "deletable")
    ids = {}
    for key, user, format, target in [
        ("own_edl", USER, "edl", "self"),
        ("own_mcf", USER, "mcf", "self"),
        ("admin_edl", ADMIN, "edl", "self"),
        ("default_mcf", ADMIN, "mcf", "item_default"),
    ]:
        response = publish(app, review, format, user=user, target=target)
        assert response.status_code == 200, response.text
        ids[key] = response.json()["id"]
    return review, ids


def files(app):
    with app.accounts.factory() as db:
        return {
            row.id: (
                row.review_id,
                row.policy_owner_id,
                row.original_author_id,
                row.actor_id,
                row.content_bytes,
                Path(row.destination_path).read_bytes()
                if Path(row.destination_path).exists()
                else None,
            )
            for row in db.query(MediaItemExportRow)
        }


def preview(app, review, user):
    response = app.send("GET", f"/api/reviews/{review.id}/deletion", app.accounts.cookie(user))
    assert response.status_code == 200, response.text
    return response.json()


def confirm(app, review, user, body, choices):
    request = {
        "revision": body["revision"],
        "confirmation": body["confirmation"],
        "files": [{"export_id": key, "choice": value} for key, value in choices.items()],
    }
    return app.send(
        "POST", f"/api/reviews/{review.id}/deletion", app.accounts.unsafe_cookie(user), request
    )


def actions(app):
    with app.accounts.factory() as db:
        return sorted(
            (row.action, row.resource)
            for row in db.query(AuditEventRow).filter(
                AuditEventRow.action.in_(["export.delete", "export.retain"])
            )
        )


def test_preview_discloses_only_visible_files_and_permitted_choices(app):
    review, ids = published(app)
    own = preview(app, review, USER)
    assert {item["publication"]["id"]: item["choices"] for item in own["files"]} == {
        ids["own_edl"]: ["remove", "retain"],
        ids["own_mcf"]: ["remove", "retain"],
    }
    assert own["legacy_formats"] == [] and len(own["scan_ids"]) == 1
    hidden = {str(ids["admin_edl"]), str(ids["default_mcf"])}
    assert not any(f'"id":{value}' in json.dumps(own, separators=(",", ":")) for value in hidden)
    admin = preview(app, review, ADMIN)
    assert {item["publication"]["id"] for item in admin["files"]} == set(ids.values())
    assert admin["confirmation"] != own["confirmation"]
    app.accounts.set_override(ADMIN, C.EXPORTS_DEFAULT_DELETE, OverrideEffect.DENY)
    limited = {
        item["publication"]["id"]: item["choices"] for item in preview(app, review, ADMIN)["files"]
    }
    assert limited[ids["default_mcf"]] == ["retain"]
    assert limited[ids["admin_edl"]] == ["remove", "retain"]
    peer = app.send("GET", f"/api/reviews/{review.id}/deletion", app.accounts.cookie(PEER_USER))
    absent = app.send("GET", "/api/reviews/absent/deletion", app.accounts.cookie(PEER_USER))
    assert peer.status_code == absent.status_code == 404
    assert peer.content == absent.content
    app.accounts.set_override(USER, C.REVIEWS_DELETE_OWN, OverrideEffect.DENY)
    denied = app.send("GET", f"/api/reviews/{review.id}/deletion", app.accounts.cookie(USER))
    assert denied.status_code == 403


@pytest.mark.parametrize("credential", ["session", "key"])
def test_retain_and_remove_change_only_selected_authorized_files(app, credential):
    review, ids = published(app)
    other = seeded_review(app, USER, "unrelated")
    assert publish(app, other, "edl").status_code == 200
    before = files(app)
    with app.accounts.factory() as db:
        removed_path = Path(db.get(MediaItemExportRow, ids["own_mcf"]).destination_path)
    body = preview(app, review, USER)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    response = app.send(
        "POST",
        f"/api/reviews/{review.id}/deletion",
        headers,
        {
            "revision": body["revision"],
            "confirmation": body["confirmation"],
            "files": [
                {"export_id": ids["own_edl"], "choice": "retain"},
                {"export_id": ids["own_mcf"], "choice": "remove"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "review_id": review.id,
        "retained": [ids["own_edl"]],
        "removed": [ids["own_mcf"]],
    }
    after = files(app)
    assert ids["own_mcf"] not in after
    for key in ["own_edl", "admin_edl", "default_mcf"]:
        old, new = before[ids[key]], after[ids[key]]
        assert new[0] is None
        assert new[1:5] == old[1:5]
        assert new[5] == old[5] == new[4]
        assert new[1] == new[2] == USER
    assert after[ids["admin_edl"]][3] == ADMIN
    unrelated = {key: value for key, value in after.items() if value[0] == other.id}
    assert len(unrelated) == 1
    assert list(unrelated.values())[0] == before[next(iter(unrelated))]
    assert not removed_path.exists()
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is None
        assert db.get(SavedReviewRow, other.id) is not None
        retained = db.get(MediaItemExportRow, ids["own_edl"])
        assert json.loads(retained.snapshot_json)["source_review_id"] == review.id
    assert actions(app) == sorted(
        [("export.delete", f"export:{ids['own_mcf']}")]
        + [
            ("export.retain", f"export:{ids[key]}")
            for key in ["own_edl", "admin_edl", "default_mcf"]
        ]
    )
    status = app.send("GET", f"/api/exports/{ids['own_edl']}", app.accounts.cookie(USER))
    assert status.status_code == 200
    assert status.json()["review_id"] is None and status.json()["serving"] is True


def test_callers_without_delete_rights_cannot_remove_default_or_cross_user_files(app):
    review, ids = published(app)
    before = files(app)
    body = preview(app, review, USER)
    choices = {ids["own_edl"]: "retain", ids["own_mcf"]: "retain"}
    smuggled = confirm(app, review, USER, body, choices | {ids["default_mcf"]: "remove"})
    assert smuggled.status_code == 422
    assert smuggled.json()["detail"]["code"] == "review_deletion_choice_required"
    smuggled = confirm(app, review, USER, body, choices | {ids["admin_edl"]: "remove"})
    assert smuggled.status_code == 422
    app.accounts.set_override(ADMIN, C.EXPORTS_DEFAULT_DELETE, OverrideEffect.DENY)
    app.accounts.set_override(ADMIN, C.EXPORTS_PERSONAL_DELETE_OTHER, OverrideEffect.DENY)
    admin = preview(app, review, ADMIN)
    assert {item["publication"]["id"] for item in admin["files"]} == {
        ids["admin_edl"],
        ids["default_mcf"],
    }
    refused = confirm(
        app, review, ADMIN, admin, {ids["admin_edl"]: "retain", ids["default_mcf"]: "remove"}
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "capability_denied"
    assert files(app) == before
    assert actions(app) == []
    retained = confirm(
        app, review, ADMIN, admin, {ids["admin_edl"]: "remove", ids["default_mcf"]: "retain"}
    )
    assert retained.status_code == 200, retained.text
    after = files(app)
    assert ids["admin_edl"] not in after
    for key in ["own_edl", "own_mcf", "default_mcf"]:
        assert after[ids[key]][5] == before[ids[key]][5] is not None


@pytest.mark.parametrize("on_disk", [True, False])
def test_disposition_audits_carry_the_confirmed_review_revision(app, on_disk):
    review, ids = published(app)
    renamed = app.send(
        "PATCH",
        f"/api/reviews/{review.id}",
        app.accounts.unsafe_cookie(USER),
        {"revision": 0, "name": "confirmed-revision-one"},
    )
    assert renamed.status_code == 200, renamed.text
    if not on_disk:
        with app.accounts.factory() as db:
            Path(db.get(MediaItemExportRow, ids["own_edl"]).destination_path).unlink()
    body = preview(app, review, USER)
    assert body["revision"] == 1
    response = confirm(
        app, review, USER, body, {ids["own_edl"]: "remove", ids["own_mcf"]: "retain"}
    )
    assert response.status_code == 200, response.text
    with app.accounts.factory() as db:
        details = {
            (row.action, row.resource): json.loads(row.details_json)
            for row in db.query(AuditEventRow).filter(
                AuditEventRow.action.in_(["export.delete", "export.retain"])
            )
        }
    assert details[("export.delete", f"export:{ids['own_edl']}")]["review_revision"] == 1
    assert details[("export.retain", f"export:{ids['own_mcf']}")]["review_revision"] == 1


@pytest.mark.parametrize("target", ["hidden", "absent", "denied", "allowed"])
def test_duplicate_choices_are_refused_only_after_review_authorization(app, target):
    review, ids = published(app)
    before = files(app)
    body = preview(app, review, USER)
    if target == "denied":
        app.accounts.set_override(USER, C.REVIEWS_DELETE_OWN, OverrideEffect.DENY)
    user = PEER_USER if target == "hidden" else USER
    review_id = "absent" if target == "absent" else review.id
    response = app.send(
        "POST",
        f"/api/reviews/{review_id}/deletion",
        app.accounts.unsafe_cookie(user),
        {
            "revision": body["revision"],
            "confirmation": body["confirmation"],
            "files": [
                {"export_id": ids["own_edl"], "choice": "remove"},
                {"export_id": ids["own_edl"], "choice": "remove"},
                {"export_id": ids["own_mcf"], "choice": "retain"},
            ],
        },
    )
    expected = {"hidden": 404, "absent": 404, "denied": 403, "allowed": 422}[target]
    assert response.status_code == expected, response.text
    if target == "allowed":
        assert response.json()["detail"]["code"] == "review_deletion_choice_required"
    assert files(app) == before
    assert actions(app) == []
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is not None


def change(app, review, ids, kind):
    if kind == "policy":
        with app.accounts.factory() as db:
            db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
            db.commit()
    elif kind == "export":
        assert publish(app, review, "edl").status_code == 200
    elif kind == "review":
        renamed = app.send(
            "PATCH",
            f"/api/reviews/{review.id}",
            app.accounts.unsafe_cookie(USER),
            {"name": "renamed", "revision": review.revision},
        )
        assert renamed.status_code == 200, renamed.text
    elif kind == "permission":
        app.accounts.set_override(USER, C.EXPORTS_PERSONAL_DELETE_OWN, OverrideEffect.DENY)
    elif kind == "new_file":
        assert publish(app, review, "mcf", user=ADMIN).status_code == 200


@pytest.mark.parametrize("kind", ["policy", "export", "review", "permission", "new_file"])
def test_stale_preview_is_refused_without_changes(app, kind):
    review = seeded_review(app, USER, "stale")
    ids = {}
    for format in ["edl", "mcf"]:
        ids[format] = publish(app, review, format).json()["id"]
    body = preview(app, review, USER)
    change(app, review, ids, kind)
    if kind == "new_file":
        app.accounts.set_override(USER, C.EXPORTS_PERSONAL_DELETE_OTHER, OverrideEffect.ALLOW)
    before = files(app)
    response = confirm(app, review, USER, body, {ids["edl"]: "remove", ids["mcf"]: "retain"})
    assert response.status_code == 409, response.text
    expected = "stale_review_revision" if kind == "review" else "stale_review_deletion"
    assert response.json()["detail"]["code"] == expected
    assert files(app) == before
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is not None
    assert actions(app) == []


def test_hidden_changes_do_not_invalidate_or_disclose(app):
    review, ids = published(app)
    body = preview(app, review, USER)
    assert publish(app, review, "edl", user=ADMIN).status_code == 200
    assert publish(app, review, "mcf", user=ADMIN, target="item_default").status_code == 200
    response = confirm(
        app, review, USER, body, {ids["own_edl"]: "retain", ids["own_mcf"]: "retain"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["retained"] == sorted([ids["own_edl"], ids["own_mcf"]])
    assert all(value[0] is None for value in files(app).values())


def test_plain_delete_refuses_visible_files_and_retains_hidden_ones(app):
    review = seeded_review(app, USER, "plain")
    admin_edl = publish(app, review, "edl", user=ADMIN).json()["id"]
    before = files(app)[admin_edl]
    deleted = app.send(
        "DELETE", f"/api/reviews/{review.id}?revision=0", app.accounts.unsafe_cookie(USER)
    )
    assert deleted.status_code == 204, deleted.text
    after = files(app)[admin_edl]
    assert after[0] is None and after[1:] == before[1:]
    assert actions(app) == [("export.retain", f"export:{admin_edl}")]
    visible = seeded_review(app, USER, "visible")
    assert publish(app, visible, "edl").status_code == 200
    refused = app.send(
        "DELETE", f"/api/reviews/{visible.id}?revision=0", app.accounts.unsafe_cookie(USER)
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "published_review_retention_required"


@pytest.mark.parametrize("route", ["plain", "explicit"])
def test_deleted_records_are_detached_without_a_choice_and_keep_ownership(app, route):
    review = seeded_review(app, USER, "deleted-record")
    export_id = publish(app, review, "edl").json()["id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        path = Path(row.destination_path)
        owned = path.read_bytes()
        revision = row.publication_revision
        publication_id = json.loads(row.snapshot_json)["publication_id"]
    path.unlink()
    gone = app.send(
        "DELETE",
        f"/api/exports/{export_id}?revision={revision}&publication_id={publication_id}",
        app.accounts.unsafe_cookie(USER),
    )
    assert gone.status_code == 204, gone.text
    audited = actions(app)
    assert audited == [("export.delete", f"export:{export_id}")]
    if route == "plain":
        response = app.send(
            "DELETE", f"/api/reviews/{review.id}?revision=0", app.accounts.unsafe_cookie(USER)
        )
        assert response.status_code == 204, response.text
    else:
        body = preview(app, review, USER)
        assert body["files"] == [] and body["legacy_formats"] == []
        response = confirm(app, review, USER, body, {})
        assert response.status_code == 200, response.text
        assert response.json()["retained"] == [] and response.json()["removed"] == []
    assert actions(app) == audited
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is None
        assert db.get(MediaItemExportRow, export_id).review_id is None
    path.write_bytes(owned)
    sweep(app)
    assert not path.exists()


@pytest.mark.parametrize("fault", ["review", "audit", "collision"])
def test_failed_deletion_restores_files_rows_and_review(app, monkeypatch, fault):
    review, ids = published(app)
    body = preview(app, review, ADMIN)
    before = files(app)
    if fault == "review":

        def fail(*_args):
            raise OperationalError("DELETE", {}, Exception("injected"))

        monkeypatch.setattr(review_disposition, "remove_review_row", fail)
    elif fault == "audit":
        original = review_disposition._event

        def fail(db, row, access, action, **kwargs):
            if action == "export.retain":
                raise OperationalError("INSERT", {}, Exception("injected"))
            return original(db, row, access, action, **kwargs)

        monkeypatch.setattr(review_disposition, "_event", fail)
    else:
        with app.accounts.factory() as db:
            path = Path(db.get(MediaItemExportRow, ids["default_mcf"]).destination_path)
        path.write_bytes(b"foreign bytes")
        before = files(app)
    choices = {ids[key]: "remove" for key in ["own_edl", "own_mcf", "admin_edl"]}
    default = "retain" if fault == "audit" else "remove"
    response = confirm(app, review, ADMIN, body, choices | {ids["default_mcf"]: default})
    expected = (
        (409, "export_file_collision") if fault == "collision" else (500, "export_write_failed")
    )
    assert response.status_code == expected[0], response.text
    assert response.json()["detail"]["code"] == expected[1]
    assert files(app) == before
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is not None
    assert actions(app) == []
    assert not list(app.media.rglob("*.editarr-recovery.json"))
    assert not list(app.media.rglob("*.bak"))


@pytest.mark.parametrize("outcome", ["regenerated", "withdrawn"])
def test_retained_files_follow_later_policy_changes(app, outcome):
    from scanner.persistence.repository.export_regeneration import (
        claim_regeneration,
        queue_regenerations,
    )
    from scanner.runtime.export_regeneration import settle_regeneration

    review, ids = published(app)
    body = preview(app, review, USER)
    response = confirm(
        app, review, USER, body, {ids["own_edl"]: "retain", ids["own_mcf"]: "retain"}
    )
    assert response.status_code == 200, response.text
    if outcome == "withdrawn":
        with app.accounts.factory() as db:
            media = Path(
                json.loads(db.get(MediaItemExportRow, ids["own_edl"]).snapshot_json)["media_path"]
            )
        media.unlink()
    with app.accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        db.commit()
        assert queue_regenerations(db) == 3
        db.commit()
    personal = {ids["own_edl"], ids["own_mcf"], ids["admin_edl"]}
    settled = set()
    for _attempt in range(3):
        with app.accounts.factory() as db:
            job = claim_regeneration(db, now=time.time() + 3600)
            if job is None:
                break
            assert settle_regeneration(db, job) in {"succeeded", "failed"}
            settled.add(job.export_id)
    assert settled == personal
    with app.accounts.factory() as db:
        for export_id in personal:
            row = db.get(MediaItemExportRow, export_id)
            path = Path(row.destination_path)
            assert row.review_id is None and row.policy_owner_id == USER
            if outcome == "regenerated":
                assert row.regeneration.state == "succeeded"
                assert row.policy_revision == 1
                assert path.read_bytes() == row.content_bytes
            else:
                assert row.regeneration.state == "withdrawn"
                assert row.content_bytes is None and not path.exists()
        default = db.get(MediaItemExportRow, ids["default_mcf"])
        assert default.regeneration is None
        assert Path(default.destination_path).read_bytes() == default.content_bytes


def test_retention_during_an_owner_change_keeps_the_new_owner_without_disclosure(app):
    """MU-36 R1: hidden rows stamped for the previous owner neither refuse nor lose intent."""
    from scanner.persistence.repository.export_regeneration import (
        claim_regeneration,
        policy_identity,
        queue_regenerations,
    )
    from scanner.runtime.export_regeneration import settle_regeneration

    review, ids = published(app)
    with app.accounts.factory() as db:
        db.get(SavedReviewRow, review.id).owner_id = PEER_USER
        db.commit()
    before = files(app)
    body = preview(app, review, PEER_USER)
    visible = {item["publication"]["id"] for item in body["files"]}
    assert visible < set(ids.values())
    response = confirm(app, review, PEER_USER, body, dict.fromkeys(visible, "retain"))
    assert response.status_code == 200, response.text
    assert set(response.json()["retained"]) == visible
    personal = {ids["own_edl"], ids["own_mcf"], ids["admin_edl"]}
    with app.accounts.factory() as db:
        for export_id in personal:
            row = db.get(MediaItemExportRow, export_id)
            assert (row.review_id, row.policy_owner_id) == (None, USER)
            assert row.intended_policy_owner_id == PEER_USER
            assert policy_identity(db, row)[0] == PEER_USER
        assert db.get(MediaItemExportRow, ids["default_mcf"]).intended_policy_owner_id is None
        assert queue_regenerations(db) == len(personal)
        db.commit()
    assert {key: value[4:] for key, value in files(app).items()} == {
        key: value[4:] for key, value in before.items()
    }
    for _attempt in personal:
        with app.accounts.factory() as db:
            job = claim_regeneration(db, now=time.time() + 3600)
            assert job is not None and job.policy_owner_id == PEER_USER
            assert settle_regeneration(db, job) == "succeeded"
    with app.accounts.factory() as db:
        assert {db.get(MediaItemExportRow, key).policy_owner_id for key in personal} == {PEER_USER}


DELETE_AND_EXIT = """
import os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from scanner.db import SavedReviewRow
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.persistence.repository.review_disposition import (
    EVERY_CHOICE, FileChoice, execute_deletion, plan_deletion,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import build_access
engine = create_engine(sys.argv[1])
with Session(engine) as db, publication_lock(db), write_lock(db):
    plan = plan_deletion(db, db.get(SavedReviewRow, sys.argv[3]), lambda row: EVERY_CHOICE)
    commit = db.commit
    def crash():
        if sys.argv[2] == "True":
            commit()
        os._exit(73)
    db.commit = crash
    choices = {file.row_id: FileChoice.REMOVE for file in plan.files}
    execute_deletion(db, plan, choices, build_access(Role.ADMIN))
"""


@pytest.mark.parametrize("committed", [False, True])
def test_hard_exit_during_removal_converges_on_the_next_writer(app, committed):
    import subprocess
    import sys

    review = seeded_review(app, USER, "crash")
    export_id = publish(app, review, "edl").json()["id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        path = Path(row.destination_path)
        source = db.get(SavedReviewRow, review.id)
        media_item_id, scan_id = source.media_item_id, source.memberships[0].scan_id
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            DELETE_AND_EXIT,
            str(app.accounts.engine.url),
            str(committed),
            review.id,
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert not path.exists()
    assert list(path.parent.glob(f".{path.name}.editarr-recovery.json"))
    with app.accounts.factory() as db:
        assert (db.get(SavedReviewRow, review.id) is None) is committed
        assert (db.get(MediaItemExportRow, export_id) is None) is committed
    if committed:
        replacement = seeded_review(app, USER, "after crash")
        with app.accounts.factory() as db:
            row = db.get(SavedReviewRow, replacement.id)
            row.media_item_id = media_item_id
            row.memberships[0].scan_id = scan_id
            db.commit()
        assert publish(app, replacement, "edl").status_code == 200
        assert path.exists()
    else:
        response = confirm(app, review, USER, preview(app, review, USER), {export_id: "remove"})
        assert response.status_code == 200, response.text
        assert not path.exists()
        assert actions(app) == [("export.delete", f"export:{export_id}")]
    assert sorted(other.name for other in path.parent.glob(f".{path.name}.*")) == []
