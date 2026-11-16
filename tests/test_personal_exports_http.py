import json
from pathlib import Path

import pytest
from sqlalchemy import text

from scanner.db import (
    AuditEventRow,
    MediaItemExportRow,
    SavedReviewRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.http import review_export_api
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_review_access_http import app as _review_app_fixture
from tests.test_review_access_http import seeded_review as _seeded_review

review_app = _review_app_fixture


def seeded_review(app, owner, name):
    review = _seeded_review(app, owner, name)
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        row.mcf_document.material_start_seconds = 0
        row.mcf_document.material_end_seconds = 60
        db.commit()
    return review


@pytest.fixture
def app(review_app):
    review_app.client.app.include_router(review_export_api.router)
    return review_app


def publish(app, review, format, *, user=USER, **kwargs):
    return app.send(
        "POST",
        f"/api/reviews/{review.id}/exports/{format}",
        app.accounts.unsafe_cookie(user),
        {"revision": review.revision, **kwargs},
    )


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_download_publication_and_deletion_match_disk_database_and_audit(app, format):
    review = seeded_review(app, USER, "personal")
    download = app.send(
        "POST",
        f"/api/reviews/{review.id}/exports/{format}/download",
        app.accounts.unsafe_cookie(USER),
        {"revision": review.revision},
    )
    assert download.status_code == 200, download.text
    assert download.headers["cache-control"] == "private, no-store"
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
    result = publish(app, review, format)
    assert result.status_code == 200, result.text
    status = result.json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        destination = Path(row.destination_path)
        assert destination.read_bytes() == row.content_bytes == download.content
        assert destination.name == f"personal.user-{row.target_export_id}.{format}"
        assert destination.parent.name == row.target_export_id
        assert destination.parent.parent.name == format
        assert not (app.media / f"personal.{format}").exists()
        snapshot = json.loads(row.snapshot_json)
        assert snapshot["source_review_id"] == review.id
        assert snapshot["review"]["base_filters"] == []
        event = db.query(AuditEventRow).filter_by(action="export.publish").one()
        assert (event.actor_id, event.target_user_id, event.media_key) == (
            USER,
            USER,
            row.media_item_id,
        )
    deleted = app.send(
        "DELETE",
        f"/api/exports/{status['id']}?revision=1&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )
    assert deleted.status_code == 204, deleted.text
    assert not destination.exists()
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
        assert db.query(AuditEventRow).filter_by(action="export.delete").count() == 1


def test_cross_user_target_does_not_change_policy_or_author(app):
    review = seeded_review(app, USER, "authored")
    refused = publish(app, review, "edl", user=PEER_USER)
    assert refused.status_code == 404
    for target, expected in [("self", ADMIN), ("review_owner", USER)]:
        result = publish(app, review, "edl", user=ADMIN, target=target)
        assert result.status_code == 200, result.text
        assert result.json()["target_user_id"] == expected
        assert result.json()["policy_owner_id"] == USER
        assert result.json()["actor_id"] == ADMIN
        assert result.json()["original_author_id"] == USER
    assert publish(app, review, "mcf", target="item_default").status_code == 403
    default = publish(app, review, "mcf", user=ADMIN, target="item_default")
    assert default.status_code == 200, default.text
    status = default.json()
    deletion = f"/api/exports/{status['id']}?revision=1&publication_id={status['publication_id']}"
    assert app.send("DELETE", deletion, app.accounts.unsafe_cookie(USER)).status_code == 403
    assert app.send("DELETE", deletion, app.accounts.unsafe_cookie(ADMIN)).status_code == 204
    personal = publish(app, review, "mcf").json()
    deletion = (
        f"/api/exports/{personal['id']}?revision=1&publication_id={personal['publication_id']}"
    )
    assert app.send("DELETE", deletion, app.accounts.unsafe_cookie(PEER_USER)).status_code == 404
    assert app.send("DELETE", deletion, app.accounts.unsafe_cookie(ADMIN)).status_code == 204
    assert publish(app, review, "edl", target_user_id=PEER_USER).status_code == 422
    assert publish(app, review, "edl", path="/arbitrary").status_code == 422


def test_replacement_confirmation_is_bound_to_both_reviews_and_current_publication(app):
    first = seeded_review(app, USER, "same")
    second = seeded_review(app, USER, "other")
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, second.id)
        old = db.get(SavedReviewRow, first.id)
        row.media_item_id = old.media_item_id
        row.memberships[0].scan_id = old.memberships[0].scan_id
        db.commit()
    assert publish(app, first, "edl").status_code == 200
    challenge = publish(app, second, "edl")
    assert challenge.status_code == 409, challenge.text
    confirmation = challenge.json()["detail"]["confirmation"]
    assert publish(app, first, "edl").status_code == 200
    assert publish(app, second, "edl", confirmation=confirmation).status_code == 409
    challenge = publish(app, second, "edl")
    confirmation = challenge.json()["detail"]["confirmation"]
    replaced = publish(app, second, "edl", confirmation=confirmation)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["review_id"] == second.id


def test_database_and_audit_failure_preserve_prior_bytes_snapshot_and_provenance(app):
    review = seeded_review(app, USER, "rollback")
    original = publish(app, review, "edl")
    assert original.status_code == 200, original.text
    with app.accounts.factory() as db:
        row = db.query(MediaItemExportRow).one()
        before = (row.content_bytes, row.snapshot_json, row.publication_revision)
        path = Path(row.destination_path)
        db.execute(
            text(
                "CREATE TRIGGER fail_publication BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'export.publish' BEGIN SELECT RAISE(ABORT, 'fault'); END"
            )
        )
        db.commit()
    failed = publish(app, review, "edl")
    assert failed.status_code == 500, failed.text
    assert path.read_bytes() == before[0]
    with app.accounts.factory() as db:
        row = db.query(MediaItemExportRow).one()
        assert (row.content_bytes, row.snapshot_json, row.publication_revision) == before
        assert db.query(AuditEventRow).filter_by(action="export.publish").count() == 1


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_linked_ids_match_plugin_namespace_and_never_use_local_ids(app, format):
    review = seeded_review(app, USER, "linked")
    with app.accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=USER,
                server_id="synthetic-server",
                provider_user_id="1234567890abcdef1234567890abcdef",
                link_state="verified",
            )
        )
        db.commit()
    result = publish(app, review, format, user=ADMIN, target="review_owner")
    assert result.status_code == 200, result.text
    assert result.json()["target_export_id"] == "1234567890abcdef1234567890abcdef"
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, result.json()["id"])
        assert (
            Path(row.destination_path).name
            == f"linked.user-1234567890abcdef1234567890abcdef.{format}"
        )


def test_retained_snapshot_survives_source_review_deletion_and_export_remains_deletable(app):
    review = seeded_review(app, USER, "retained")
    result = publish(app, review, "edl")
    assert result.status_code == 200, result.text
    status = result.json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        path = Path(row.destination_path)
        snapshot, content = row.snapshot_json, row.content_bytes
        row.review_id = row.exported_format_revision = None
        db.flush()
        db.delete(db.get(SavedReviewRow, review.id))
        db.commit()
    own = app.send("GET", f"/api/exports/{status['id']}", app.accounts.cookie(USER))
    assert own.status_code == 200, own.text
    hidden = app.send("GET", f"/api/exports/{status['id']}", app.accounts.cookie(PEER_USER))
    assert hidden.status_code == 404
    assert str(app.media) not in own.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        assert row.snapshot_json == snapshot
        assert row.content_bytes == content == path.read_bytes()
    deleted = app.send(
        "DELETE",
        f"/api/exports/{status['id']}?revision=1&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )
    assert deleted.status_code == 204, deleted.text
    assert not path.exists()


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_unowned_files_and_symlinks_are_never_overwritten(app, format):
    review = seeded_review(app, USER, "collision")
    with app.accounts.factory() as db:
        identity = db.get(UserRow, USER).local_export_id
    destination = app.media / format / identity / f"collision.user-{identity}.{format}"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"not ours")
    refused = publish(app, review, format)
    assert refused.status_code == 409, refused.text
    assert destination.read_bytes() == b"not ours"
    destination.unlink()
    destination.symlink_to(app.media / "collision.mkv")
    refused = publish(app, review, format)
    assert refused.status_code >= 400
    assert destination.is_symlink()
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
        assert (
            db.query(AuditEventRow).filter_by(action="export.publish", outcome="succeeded").count()
            == 0
        )


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("operation", ["republish", "delete"])
def test_symlinked_publication_returns_conflict_without_changes(app, format, operation):
    review = seeded_review(app, USER, "symlinked")
    published = publish(app, review, format)
    assert published.status_code == 200, published.text
    status = published.json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        before = (row.content_bytes, row.snapshot_json, row.publication_revision)
        path = Path(row.destination_path)
        audit_count = db.query(AuditEventRow).count()
    target = app.media / "untouched-target"
    target.write_bytes(b"not an export\n")
    path.unlink()
    path.symlink_to(target)

    if operation == "republish":
        refused = publish(app, review, format)
    else:
        refused = app.send(
            "DELETE",
            f"/api/exports/{status['id']}?revision={status['publication_revision']}"
            f"&publication_id={status['publication_id']}",
            app.accounts.unsafe_cookie(USER),
        )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "export_destination_changed"
    assert path.is_symlink() and path.readlink() == target
    assert target.read_bytes() == b"not an export\n"
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        assert (row.content_bytes, row.snapshot_json, row.publication_revision) == before
        assert db.query(AuditEventRow).count() == audit_count


def test_concurrent_confirmations_cannot_replace_an_unintended_publication(app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    reviews = [seeded_review(app, USER, name) for name in ("concurrent", "second", "third")]
    with app.accounts.factory() as db:
        source = db.get(SavedReviewRow, reviews[0].id)
        for review in reviews[1:]:
            row = db.get(SavedReviewRow, review.id)
            row.media_item_id = source.media_item_id
            row.memberships[0].scan_id = source.memberships[0].scan_id
        db.commit()
    assert publish(app, reviews[0], "edl").status_code == 200
    challenges = [
        publish(app, review, "edl").json()["detail"]["confirmation"] for review in reviews[1:]
    ]
    barrier = Barrier(2)
    headers = app.accounts.unsafe_cookie(USER)

    def attempt(pair):
        review, confirmation = pair
        barrier.wait(timeout=10)
        return app.client.post(
            f"/api/reviews/{review.id}/exports/edl",
            headers=headers,
            json={"revision": 0, "confirmation": confirmation},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(attempt, zip(reviews[1:], challenges)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    with app.accounts.factory() as db:
        row = db.query(MediaItemExportRow).one()
        assert row.review_id == winner["review_id"]
        assert row.publication_revision == 2
        assert Path(row.destination_path).read_bytes() == row.content_bytes
        events = db.query(AuditEventRow).filter_by(action="export.publish").all()
        assert len(events) == 2
        assert all(event.actor_id == event.target_user_id == USER for event in events)


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("failure", ["replace", "commit", "restore", "commit_ack"])
def test_file_commit_and_failed_rollback_leave_recoverable_bytes(app, monkeypatch, failure, format):
    from sqlalchemy.orm import Session

    import scanner.persistence.repository.export_publication as publication
    import scanner.persistence.repository.export_recovery as recovery

    review = seeded_review(app, USER, "fault")
    original = publish(app, review, format)
    assert original.status_code == 200, original.text
    with app.accounts.factory() as db:
        row = db.query(MediaItemExportRow).one()
        path = Path(row.destination_path)
        before = (row.content_bytes, row.snapshot_json, row.publication_revision)
    replace = recovery.os.replace
    commit = Session.commit
    prepare = publication.prepare_review_export

    def changed_content(*args, **kwargs):
        output = prepare(*args, **kwargs)
        from dataclasses import replace as replace_output

        return replace_output(output, content=output.content + b"\n")

    def fail_replace(source, destination):
        if (failure == "replace" and str(source).endswith(".tmp")) or (
            failure == "restore" and str(source).endswith(".bak")
        ):
            raise OSError("injected filesystem failure")
        return replace(source, destination)

    def fail_commit(session):
        if failure in {"commit", "restore"}:
            raise RuntimeError("injected commit failure")
        if failure == "commit_ack":
            commit(session)
            raise RuntimeError("injected lost commit acknowledgment")
        return commit(session)

    with monkeypatch.context() as fault:
        fault.setattr(publication, "prepare_review_export", changed_content)
        fault.setattr(recovery.os, "replace", fail_replace)
        fault.setattr(Session, "commit", fail_commit)
        response = publish(app, review, format)
        assert response.status_code in {409, 500}, response.text
    with app.accounts.factory() as db:
        row = db.query(MediaItemExportRow).one()
        if failure == "commit_ack":
            assert row.publication_revision == before[2] + 1
            assert path.read_bytes() == row.content_bytes
        else:
            assert (row.content_bytes, row.snapshot_json, row.publication_revision) == before
        assert db.query(AuditEventRow).filter_by(action="export.publish").count() == (
            2 if failure == "commit_ack" else 1
        )
    if failure == "restore":
        assert list(path.parent.glob("*.bak")) or list(path.parent.glob(".*.bak"))
        assert list(path.parent.glob(".*.editarr-recovery.json"))
    retry = publish(app, review, format)
    assert retry.status_code == 200, retry.text
    assert path.read_bytes() == before[0]
    assert not list(path.parent.glob(".*.bak"))
    assert not list(path.parent.glob(".*.editarr-recovery.json"))


def test_delete_audit_failure_preserves_file_and_publication(app):
    review = seeded_review(app, USER, "delete-rollback")
    status = publish(app, review, "mcf").json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        path, content, snapshot = Path(row.destination_path), row.content_bytes, row.snapshot_json
        db.execute(
            text(
                "CREATE TRIGGER fail_deletion BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'export.delete' BEGIN SELECT RAISE(ABORT, 'fault'); END"
            )
        )
        db.commit()
    response = app.send(
        "DELETE",
        f"/api/exports/{status['id']}?revision=1&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )
    assert response.status_code == 500, response.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, status["id"])
        assert row.snapshot_json == snapshot
        assert path.read_bytes() == row.content_bytes == content
        assert db.query(AuditEventRow).filter_by(action="export.delete").count() == 0


def test_stale_delete_cannot_remove_a_recreated_export(app):
    first = publish(app, seeded_review(app, USER, "first-generation"), "edl").json()
    identity = first["publication_id"]
    url = f"/api/exports/{first['id']}?revision=1&publication_id={identity}"
    assert app.send("DELETE", url, app.accounts.unsafe_cookie(USER)).status_code == 204
    second = publish(app, seeded_review(app, USER, "second-generation"), "edl").json()
    assert first["id"] == second["id"]
    assert identity != second["publication_id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, second["id"])
        destination = Path(row.destination_path)
        before = (row.content_bytes, row.snapshot_json, row.publication_revision)
        events = [(event.id, event.action, event.details_json) for event in db.query(AuditEventRow)]
    stale = app.send("DELETE", url, app.accounts.unsafe_cookie(USER))
    assert stale.status_code == 409, stale.text
    assert destination.read_bytes() == before[0]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, second["id"])
        assert (row.content_bytes, row.snapshot_json, row.publication_revision) == before
        assert [
            (event.id, event.action, event.details_json) for event in db.query(AuditEventRow)
        ] == events
    assert (
        app.send("GET", f"/api/exports/{second['id']}", app.accounts.cookie(USER)).status_code
        == 200
    )


@pytest.mark.parametrize("download", [False, True])
def test_cross_user_exports_conjoin_live_own_capability(app, download):
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect

    review = seeded_review(app, USER, "revoked")
    capability = C.EXPORTS_PERSONAL_DOWNLOAD_OWN if download else C.EXPORTS_PERSONAL_PUBLISH_OWN
    app.accounts.set_override(ADMIN, capability, OverrideEffect.DENY)
    suffix = "/download" if download else ""
    response = app.send(
        "POST",
        f"/api/reviews/{review.id}/exports/edl{suffix}",
        app.accounts.unsafe_cookie(ADMIN),
        {"revision": review.revision},
    )
    assert response.status_code == 403, response.text
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0


@pytest.mark.parametrize("endpoint", ["edl", "mcf", "exports/edl", "exports/mcf"])
def test_own_publication_uses_live_personal_publish_capability(app, endpoint):
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect

    review = seeded_review(app, USER, "own-capability")
    app.accounts.set_override(USER, C.EXPORTS_PERSONAL_PUBLISH_OWN, OverrideEffect.DENY)
    response = app.send(
        "POST",
        f"/api/reviews/{review.id}/{endpoint}",
        app.accounts.unsafe_cookie(USER),
        {"revision": review.revision},
    )
    assert response.status_code == 403, response.text
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
