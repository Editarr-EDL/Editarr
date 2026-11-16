"""Deletion, detached evidence, and coverage through persisted review operations."""

import sqlite3

import pytest

from scanner import repository
from scanner.backup.archive import export_backup
from scanner.backup.snapshots import create_safety_snapshot
from scanner.db import MediaItemRow, ReviewFilterRow, SavedReviewRow, ScanFindingRow, ScanRow
from scanner.models import EditAction, McfChannel, ReviewDuplicate
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.preflight import inspect_schema
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from tests.community_identity_migration_support import no_network as no_network
from tests.multi_user_access_support import PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app
from tests.test_review_policy_binding import import_review
from tests.test_review_policy_view import draft, enforce
from tests.test_review_policy_view import policy_review as policy_review


def test_http_review_delete_keeps_startup_admission(app, monkeypatch, tmp_path):
    detail = import_review(
        app, monkeypatch, app.jellyfin_item(guid("ledger-delete"), LIBRARY_A), b"1 2 1\n3 4 3\n"
    )
    headers = app.accounts.unsafe_cookie(USER)
    scan_id = detail["memberships"][0]["scan_id"]
    blocked = app.send("DELETE", f"/api/scans/{scan_id}", headers)
    assert blocked.status_code == 409, blocked.text
    deleted = app.send(
        "DELETE", f"/api/reviews/{detail['id']}?revision={detail['revision']}", headers
    )
    assert deleted.status_code == 204, deleted.text
    deleted_scan = app.send("DELETE", f"/api/scans/{scan_id}", headers)
    assert deleted_scan.status_code in {200, 204}, deleted_scan.text
    with sqlite3.connect(app.accounts.path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    assert inspect_schema(app.accounts.path, tmp_path) == adoption.HEAD_REVISION
    adoption.upgrade_existing_database(app.accounts.path, tmp_path)
    create_safety_snapshot(app.accounts.path, tmp_path)
    export_backup(app.accounts.path, tmp_path, tmp_path / "exports" / "deleted.editarr-backup")


def test_scan_removed_before_enforcement_blocks_output_until_readded(policy_review):
    """Historical probe name retained; the coordinator rules this removal legitimate."""
    accounts, review_id = policy_review
    with accounts.factory() as db:
        original = repository.get_saved_review(db, review_id)
        repository.save_saved_review(db, review_id, draft(original, memberships=[], filters=[]))
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert not detail.policy_missing_evidence
        assert not repository.get_scan_references(db, "scan").reviews
        assert repository.delete_scan(db, "scan")
    with sqlite3.connect(accounts.path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_tombstone_stops_referencing_scan_when_policy_stops_requiring_it(policy_review):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        with pytest.raises(ReviewPolicyConflict):
            repository.save_saved_review(db, review_id, draft(detail, memberships=[], filters=[]))
    enforce(accounts, rules=[])
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        repository.save_saved_review(db, review_id, draft(detail, memberships=[], filters=[]))
    enforce(accounts)
    with accounts.factory() as db:
        assert not repository.get_saved_review(db, review_id).policy_missing_evidence
        assert repository.delete_scan(db, "scan")


@pytest.mark.parametrize("different_owner", [False, True])
def test_copies_inherit_only_current_bindings_and_destination_required_tombstones(
    policy_review, different_owner
):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        row = db.get(SavedReviewRow, review_id)
        row.memberships.clear()
        row.filters.clear()
        db.commit()
        source = repository.get_saved_review(db, review_id)
        assert source.policy_missing_evidence
        assert repository.get_scan_references(db, "scan").reviews
        db.info["review_actor"] = ActivityOwner(PEER_USER if different_owner else USER, 1)
        copied = repository.duplicate_saved_review(
            db,
            review_id,
            ReviewDuplicate(
                revision=source.revision,
                name="Tombstone copy",
                policy_owner_id=source.policy_owner_id,
                policy_revision=source.policy_revision,
            ),
        )
        assert bool(copied.policy_missing_evidence) is not different_owner
    enforce(accounts, rules=[])
    with accounts.factory() as db:
        assert not repository.get_scan_references(db, "scan").reviews
        assert repository.delete_scan(db, "scan")
    with sqlite3.connect(accounts.path) as database:
        assert database.execute("SELECT count(*) FROM review_policy_evidence").fetchone()[0] == 0
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_copy_resolves_staged_memberships_with_autoflush_disabled(policy_review):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.autoflush = False
        original = repository.get_saved_review(db, review_id)
        copied = repository.duplicate_saved_review(
            db,
            review_id,
            ReviewDuplicate(
                revision=original.revision,
                name="No-autoflush copy",
                policy_owner_id=original.policy_owner_id,
                policy_revision=original.policy_revision,
            ),
        )
        assert copied.memberships == original.memberships
        assert not copied.policy_missing_evidence


@pytest.mark.parametrize("channel", [McfChannel.VIDEO, McfChannel.BOTH, McfChannel.AUDIO])
def test_required_video_skip_cannot_be_weakened_to_mute_or_audio(policy_review, channel):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        finding = db.query(ScanFindingRow).filter_by(scan_id="scan").one()
        finding.edit_action = EditAction.SKIP
        finding.mcf_channel = McfChannel.VIDEO
        saved_filter = db.query(ReviewFilterRow).filter_by(review_id=review_id).one()
        saved_filter.edit_action = EditAction.SKIP
        saved_filter.mcf_channel = McfChannel.VIDEO
        db.commit()
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        for action in (EditAction.MUTE, EditAction.SKIP):
            proposed = detail.filters[0].model_copy(
                update={"mcf_channel": channel, "edit_action": action}
            )
            if channel == McfChannel.AUDIO or action == EditAction.MUTE:
                with pytest.raises(ReviewPolicyConflict):
                    repository.save_saved_review(
                        db, review_id, draft(detail, filters=[proposed.model_dump()])
                    )
            else:
                detail = repository.save_saved_review(
                    db, review_id, draft(detail, filters=[proposed.model_dump()])
                )
                assert not detail.policy_missing_evidence


@pytest.mark.parametrize("channel", [McfChannel.AUDIO, McfChannel.VIDEO, McfChannel.BOTH])
@pytest.mark.parametrize("action", [EditAction.MUTE, EditAction.SKIP])
def test_required_audio_cue_channel_cannot_be_moved_off_audio(policy_review, channel, action):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        proposed = detail.filters[0].model_copy(
            update={"mcf_channel": channel, "edit_action": action}
        )
        if channel == McfChannel.VIDEO:
            with pytest.raises(ReviewPolicyConflict):
                repository.save_saved_review(
                    db, review_id, draft(detail, filters=[proposed.model_dump()])
                )
        else:
            saved = repository.save_saved_review(
                db, review_id, draft(detail, filters=[proposed.model_dump()])
            )
            assert not saved.policy_missing_evidence
            assert saved.filters[0].edit_action == action


def test_output_preparation_refuses_wrong_channel_even_without_a_save(policy_review):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.get(MediaItemRow, "media").path = db.get(ScanRow, "scan").resolved_local_path
        db.query(ReviewFilterRow).filter_by(
            review_id=review_id
        ).one().mcf_channel = McfChannel.VIDEO
        db.commit()
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert detail.policy_missing_evidence
        with pytest.raises(ReviewPolicyConflict) as refused:
            repository.export_saved_review_edl(
                db,
                review_id,
                revision=detail.revision,
                policy_owner_id=detail.policy_owner_id,
                policy_revision=detail.policy_revision,
            )
        assert refused.value.code == "review_policy_evidence_missing"


@pytest.mark.parametrize("narrowing,accepted", [(5e-7, False), (2e-6, False)])
def test_coverage_tolerance_does_not_admit_real_narrowing(policy_review, narrowing, accepted):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        proposed = detail.filters[0].model_copy(
            update={"start_seconds": 1 + narrowing, "end_seconds": 2 - narrowing}
        )
        if accepted:
            saved = repository.save_saved_review(
                db, review_id, draft(detail, filters=[proposed.model_dump()])
            )
            assert not saved.policy_missing_evidence
        else:
            with pytest.raises(ReviewPolicyConflict):
                repository.save_saved_review(
                    db, review_id, draft(detail, filters=[proposed.model_dump()])
                )
