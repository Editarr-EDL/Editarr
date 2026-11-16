"""Account deletion names only what the actor may see and records what they confirmed.

Covers MU-38 review findings F1 to F8 through the real HTTP routes, the real
settings backup and restore services, and the real Jellyfin admission service.
The provider snapshot of the re-admitted user is a labelled synthetic boundary.
"""

import json
from pathlib import Path

from scanner.db import MediaItemExportRow, SavedReviewRow, UserProviderIdentityRow
from scanner.persistence.repository import review_disposition
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_account_deletion_http import app as app
from tests.test_account_deletion_http import (
    bulk,
    code,
    confirmed,
    delete,
    deletion_audits,
    exports_app,
    preview,
)
from tests.test_personal_exports_http import publish, review_app, seeded_review

__all__ = ["exports_app", "review_app"]


def test_hidden_scans_jobs_and_reviews_are_counts_in_previews_results_and_replays(app):
    hidden_scan, _ = app.seed_scan(USER, "private", library=LIBRARY_B, status="complete")
    hidden_job, _ = app.seed_scan(USER, "private-job", library=LIBRARY_B, status="queued")
    shown_job, _ = app.seed_scan(USER, "shared-job", library=LIBRARY_A, status="queued")
    review = seeded_review(app, USER, "private-review")
    app.limit_to_library(ADMIN, LIBRARY_A)
    app.accounts.set_override(ADMIN, C.REVIEWS_VIEW_OTHER, OverrideEffect.DENY)
    assert (
        app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(ADMIN)).status_code == 404
    )
    deleting = preview(app, ADMIN, USER, "delete")
    assert deleting.status_code == 200, deleting.text
    for secret in (hidden_scan, hidden_job, review.id):
        assert secret not in deleting.text
    body = deleting.json()
    assert body["scans"]["hidden"] >= 2 and body["jobs"]["hidden"] == 1
    assert body["jobs"]["waiting"] == [shown_job]
    assert body["reviews"] == {"count": 1, "hidden": 1, "ids": []}
    retaining = preview(app, ADMIN, USER, "retain").json()
    response = delete(app, ADMIN, USER, "retain", retaining["confirmation"])
    assert response.status_code == 200, response.text
    replay = delete(app, ADMIN, USER, "retain", retaining["confirmation"])
    for text in (response.text, replay.text, json.dumps(retaining)):
        for secret in (hidden_scan, hidden_job, review.id):
            assert secret not in text
    result = response.json()
    assert (result["reviews_retained"], result["reviews_hidden"]) == ([], 1)
    assert (result["jobs_stopped"], result["jobs_hidden"]) == ([shown_job], 1)
    with app.accounts.factory() as db:
        inventory = json.loads(db.get(AccountDispositionRow, USER).result_json)["inventory"]
    assert inventory["reviews"] == [review.id] and hidden_job in inventory["jobs_stopped"]


def test_stored_impact_is_the_confirmed_preview_with_its_file_inventory(app):
    review = seeded_review(app, USER, "record")
    publication = publish(app, review, "edl").json()
    body = preview(app, ADMIN, USER, "delete").json()
    assert [item["publication"]["id"] for item in body["files"]] == [publication["id"]]
    result = delete(
        app, ADMIN, USER, "delete", body["confirmation"], [(publication["id"], "remove")]
    )
    assert result.status_code == 200, result.text
    with app.accounts.factory() as db:
        assert json.loads(db.get(AccountDispositionRow, USER).impact_json) == body


def test_a_new_publication_or_credential_change_invalidates_a_retention_preview(app):
    review = seeded_review(app, USER, "late-publication")
    body = preview(app, ADMIN, USER, "retain").json()
    assert publish(app, review, "edl").status_code == 200
    assert code(delete(app, ADMIN, USER, "retain", body["confirmation"])) == (
        409,
        "stale_account_deletion",
    )
    body = preview(app, ADMIN, USER, "retain").json()
    with app.accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=USER, server_id="server", provider_user_id="e" * 32, link_state="verified"
            )
        )
        db.commit()
    assert code(delete(app, ADMIN, USER, "retain", body["confirmation"]))[1] == (
        "stale_account_deletion"
    )
    assert [row[3]["reason"] for row in deletion_audits(app)] == ["stale_account_deletion"] * 2


def test_bulk_reports_each_target_after_a_later_file_failure(app, monkeypatch):
    review = seeded_review(app, USER, "io-failure")
    publication = publish(app, review, "edl").json()
    first = preview(app, ADMIN, PEER_USER, "retain").json()
    second = preview(app, ADMIN, USER, "delete").json()

    def fail(*_args, **_kwargs):
        raise PermissionError(13, "injected unwritable export directory")

    monkeypatch.setattr(review_disposition, "stage_removal", fail)
    response = bulk(
        app,
        ADMIN,
        [
            {"user_id": PEER_USER, "mode": "retain", "confirmation": first["confirmation"]},
            {
                "user_id": USER,
                "mode": "delete",
                "confirmation": second["confirmation"],
                "files": [{"export_id": publication["id"], "choice": "remove"}],
            },
            {"user_id": OWNER, "mode": "retain", "confirmation": first["confirmation"]},
        ],
    )
    assert response.status_code == 200, response.text
    outcomes = [
        (item["user_id"], item["outcome"], item["code"]) for item in response.json()["results"]
    ]
    assert outcomes == [
        (PEER_USER, "applied", None),
        (USER, "refused", "export_io_failed"),
        (OWNER, "refused", "protected_account"),
    ]
    with app.accounts.factory() as db:
        assert db.get(AccountDispositionRow, USER) is None
        assert Path(db.get(MediaItemExportRow, publication["id"]).destination_path).exists()
    assert [(row[0], row[2], row[3].get("reason")) for row in deletion_audits(app)] == [
        ("succeeded", PEER_USER, None),
        ("denied", USER, "export_io_failed"),
        ("denied", OWNER, "protected_account"),
    ]


def test_a_refused_file_collision_is_audited_once(app):
    review = seeded_review(app, USER, "foreign-file")
    publication = publish(app, review, "edl").json()
    body = preview(app, ADMIN, USER, "delete").json()
    with app.accounts.factory() as db:
        path = Path(db.get(MediaItemExportRow, publication["id"]).destination_path)
    path.write_bytes(b"unrecognized external bytes")
    response = delete(
        app, ADMIN, USER, "delete", body["confirmation"], [(publication["id"], "remove")]
    )
    assert code(response) == (409, "export_file_collision")
    assert path.read_bytes() == b"unrecognized external bytes"
    assert [(row[0], row[3]) for row in deletion_audits(app)] == [
        ("denied", {"reason": "export_file_collision"})
    ]


def test_kept_counts_describe_only_files_still_kept(app):
    review = seeded_review(app, USER, "mixed")
    edl, mcf = (publish(app, review, fmt).json()["id"] for fmt in ("edl", "mcf"))
    body = preview(app, ADMIN, USER, "delete").json()
    assert body["personal_files"] == 2
    result = delete(
        app, ADMIN, USER, "delete", body["confirmation"], [(edl, "retain"), (mcf, "remove")]
    )
    assert result.json()["personal_files_kept"] == 1
    other = seeded_review(app, PEER_USER, "removed-both")
    ids = [publish(app, other, fmt, user=PEER_USER).json()["id"] for fmt in ("edl", "mcf")]
    _body, removed = confirmed(app, ADMIN, PEER_USER, "delete", [(key, "remove") for key in ids])
    assert removed["personal_files_kept"] == 0
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).filter_by(target_user_id=PEER_USER).count() == 0


def test_a_readmitted_jellyfin_identity_replaces_retained_files_only_after_confirmation(app):
    from dataclasses import replace
    from datetime import UTC, datetime

    from scanner.formats.export_layout import parse_jellyfin_user_id
    from scanner.security.access.identity import CredentialKind, JellyfinServerId, ProviderIdentity
    from scanner.security.jellyfin_accounts import admission, reads
    from tests.media_access_support import jellyfin_user

    guid = "1234567890abcdef1234567890abcdef"
    review = seeded_review(app, USER, "retained-linked")
    with app.accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=USER, server_id="server", provider_user_id=guid, link_state="verified"
            )
        )
        db.commit()
    original = publish(app, review, "edl", user=ADMIN, target="review_owner").json()
    confirmed(app, ADMIN, USER, "retain")
    now = datetime.now(UTC)
    identity = ProviderIdentity(JellyfinServerId("server"), parse_jellyfin_user_id(guid))
    snapshot = replace(jellyfin_user("Casey"), identity=identity, avatar_tag=None, verified_at=now)
    with app.accounts.factory() as db:
        read = reads.begin_read(db, lambda: now)
        admitted = admission.import_users(
            db, None, (snapshot,), None, actor=OWNER, credential=CredentialKind.SESSION, read=read
        )[0]
        assert admitted.outcome == admission.ImportOutcome.IMPORTED
    new_user = admitted.user_id
    new_review = seeded_review(app, new_user, "readmitted")
    with app.accounts.factory() as db:
        source, target = db.get(SavedReviewRow, review.id), db.get(SavedReviewRow, new_review.id)
        target.media_item_id = source.media_item_id
        target.memberships[0].scan_id = source.memberships[0].scan_id
        db.commit()
        path = Path(db.get(MediaItemExportRow, original["id"]).destination_path)
    retained = path.read_bytes()
    asked = publish(app, new_review, "edl", user=ADMIN, target="review_owner")
    assert asked.status_code == 409, asked.text
    detail = asked.json()["detail"]
    assert detail["code"] == "export_confirmation_required" and path.read_bytes() == retained
    replaced = publish(
        app,
        new_review,
        "edl",
        user=ADMIN,
        target="review_owner",
        confirmation=detail["confirmation"],
    )
    assert replaced.status_code == 200, replaced.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, original["id"])
        assert (row.target_user_id, row.review_id) == (new_user, new_review.id)
        assert row.publication_revision == original["publication_revision"] + 1
        assert path.read_bytes() == row.content_bytes


def test_backup_and_restore_keep_deleted_accounts_deleted(app, tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from scanner.backup.archive import _sqlite_snapshot

    confirmed(app, ADMIN, USER, "retain")
    backup = tmp_path / "full.sqlite3"
    _sqlite_snapshot(app.accounts.path, backup)
    engine = create_engine(f"sqlite:///{backup}", connect_args={"check_same_thread": False})
    try:
        with monkeypatch.context() as patch:
            patch.setattr(app.accounts, "factory", sessionmaker(bind=engine))
            # The snapshot keeps keys; restore activation revokes sessions.
            listed = app.send("GET", "/api/users", app.accounts.key(ADMIN)).json()
            assert USER not in {item["id"] for item in listed["items"]}
            assert app.send("GET", f"/api/users/{USER}", app.accounts.key(ADMIN)).status_code == 404
    finally:
        engine.dispose()


def test_real_activation_restores_the_disposition_record(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from scanner.backup.archive import export_backup
    from scanner.security.account_repository import list_accounts
    from tests.test_account_disposition_migration import _disposition
    from tests.test_backup_activation import _fixture, _restore

    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    with sqlite3.connect(source / "scanner.db") as db:
        db.execute(
            "INSERT INTO users (id, role, status, disable_cause, "
            "local_export_id, permission_revision, created_at, updated_at) "
            "VALUES (2, 'user', 'disabled', 'local', ?, 2, '2026-01-01', '2026-01-01')",
            ("local-" + "8" * 32,),
        )
        _disposition(db, 2, actor_id=1)
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    engine = create_engine("sqlite:///" + str(target / "scanner.db"))
    try:
        with Session(engine) as db:
            _total, records = list_accounts(db, offset=0, limit=100, search=None)
            assert 2 not in [record.user_id for record in records]
            assert db.get(AccountDispositionRow, 2).actor_id == 1
    finally:
        engine.dispose()
