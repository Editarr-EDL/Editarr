"""Current-owner policy overlays leave independently read base rows unchanged."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from scanner import repository
from scanner.db import MediaItemRow, ReviewFilterRow, ScanFindingRow, ScanRow
from scanner.models import (
    FilterDefaultsSettings,
    McfCategory,
    McfSeverityDefault,
    ReviewCreate,
    ReviewSave,
    ReviewScanMembership,
)
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.filter_enforcement import set_enforcement
from scanner.persistence.repository.repository_settings import replace_filter_defaults
from scanner.settings.filter_policy.updates import FilterPolicyUpdate
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER, live_accounts


@pytest.fixture
def policy_review(tmp_path):
    accounts = live_accounts(tmp_path)
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"synthetic policy media")
    with accounts.factory() as db:
        db.info["review_actor"] = ActivityOwner(USER, 1)
        db.add(
            MediaItemRow(
                id="media", server_id="server", jellyfin_id="item", name="Movie", item_type="Movie"
            )
        )
        db.flush()
        db.add(
            ScanRow(
                id="scan",
                media_item_id="media",
                scan_number=1,
                source_path=str(media_path),
                resolved_local_path=str(media_path),
                status="complete",
                duration_seconds=100,
                scan_audio=True,
                scan_video=False,
            )
        )
        db.flush()
        db.add(
            ScanFindingRow(
                scan_id="scan",
                source="audio",
                category="language",
                subcategory="swearing",
                severity="low",
                mcf_channel="audio",
                label="Evidence",
                start_seconds=1,
                end_seconds=2,
                edit_action="mute",
                selected=False,
            )
        )
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Policy review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
    yield accounts, created.id
    accounts.close()


def enforce(accounts, *, rules=None, severities=()):
    """Write a real instance rule and a current-revision policy for the review owner."""
    with accounts.factory() as db:
        settings = repository.get_structured_filter_defaults_settings(db)
        chosen = rules if rules is not None else [rule("deny", category="language")]
        replace_filter_defaults(
            db,
            FilterDefaultsSettings(
                revision=settings.revision,
                selection_rules=chosen,
                severity_defaults=[
                    McfSeverityDefault(subcategory=category, severity="high")
                    for category in McfCategory
                ],
            ),
        )
        from scanner.persistence.repository.filter_preferences import enforced_filter_policy

        policy = enforced_filter_policy(db, USER)
        change = set_enforcement(
            db,
            USER,
            FilterPolicyUpdate(
                policy_revision=policy.policy_revision,
                rules=[item["id"] for item in chosen],
                severities=list(severities),
            ),
            now=datetime.now(timezone.utc),
        )
        db.commit()
        return change


def base_rows(accounts, review_id):
    with accounts.factory() as db:
        return db.execute(
            select(ReviewFilterRow.__table__).where(ReviewFilterRow.review_id == review_id)
        ).all()


def test_existing_review_follows_owner_policy_without_rewriting_base(policy_review):
    accounts, review_id = policy_review
    before = base_rows(accounts, review_id)
    enforce(accounts, severities=["swearing"])

    with accounts.factory() as db:
        review = repository.get_saved_review(db, review_id)
        assert review.filters[0].selected is True
        assert review.filters[0].severity.value == "high"
        assert review.policy_required_filter_ids == [review.filters[0].id]
        assert review.policy_locked_severities == {review.filters[0].id: "high"}
        assert review.owner_id == USER
        assert review.original_author_id == USER

    assert base_rows(accounts, review_id) == before


@pytest.mark.parametrize("initially_locked", [False, True])
def test_lock_display_uses_the_same_policy_as_the_prepared_revision(
    policy_review, monkeypatch, initially_locked
):
    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"] if initially_locked else [])
    from scanner.persistence.repository import repository_review_core

    prepare = repository_review_core.prepare_review_policy
    prepared_revision = None

    def change_after_preparation(db, row):
        nonlocal prepared_revision
        prepared = prepare(db, row)
        prepared_revision = prepared.policy_revision
        enforce(accounts, severities=[] if initially_locked else ["swearing"])
        return prepared

    monkeypatch.setattr(repository_review_core, "prepare_review_policy", change_after_preparation)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert detail.policy_revision == prepared_revision
        assert detail.policy_locked_severities == (
            {detail.filters[0].id: "high"} if initially_locked else {}
        )
    monkeypatch.setattr(repository_review_core, "prepare_review_policy", prepare)
    with accounts.factory() as db:
        fresh = repository.get_saved_review(db, review_id)
        assert fresh.policy_revision > prepared_revision


@pytest.mark.parametrize(
    "field,value",
    [
        ("label", "Harmless"),
        ("category", "violence"),
        ("mcf_channel", "video"),
        ("severity", "medium"),
    ],
)
def test_source_policy_survives_alternate_saved_metadata(policy_review, field, value):
    accounts, review_id = policy_review
    enforce(
        accounts,
        rules=[rule("deny", category="language", label_contains="Evidence", mcf_channel="audio")],
        severities=["swearing"],
    )
    with accounts.factory() as db:
        row = db.query(ReviewFilterRow).filter_by(review_id=review_id).one()
        setattr(row, field, value)
        if field == "category":
            row.subcategory = "violence"
        db.commit()
    before = base_rows(accounts, review_id)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert detail.filters[0].selected
        assert detail.filters[0].severity.value == "high"
        assert detail.policy_required_filter_ids == [detail.filters[0].id]
        assert detail.policy_locked_severities == {detail.filters[0].id: "high"}
    assert base_rows(accounts, review_id) == before


def test_missing_source_fails_closed_under_policy(policy_review):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        row = db.query(ReviewFilterRow).filter_by(review_id=review_id).one()
        row.source_finding_id = None
        db.commit()
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert detail.policy_missing_evidence[0].source_finding_id is None


def test_removed_filter_cannot_make_required_evidence_disappear(policy_review):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.query(ReviewFilterRow).filter_by(review_id=review_id).delete()
        db.commit()
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert not detail.filters
        assert detail.policy_missing_evidence[0].source_scan_id == "scan"


def draft(detail, **updates):
    values = detail.model_dump(
        include={
            "revision",
            "name",
            "memberships",
            "filters",
            "mcf_document",
            "synchronization",
            "accepted_credits",
            "policy_owner_id",
            "policy_revision",
        }
    )
    values.update(updates)
    return ReviewSave.model_validate(values)


def test_effective_roundtrip_preserves_base_and_policy_removal_restores_it(policy_review):
    accounts, review_id = policy_review
    before = base_rows(accounts, review_id)
    enforce(accounts, severities=["swearing"])
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        saved = repository.save_saved_review(db, review_id, draft(detail, name="Renamed"))
        assert saved.filters[0].selected
    assert base_rows(accounts, review_id) == before
    enforce(accounts, rules=[])
    with accounts.factory() as db:
        restored = repository.get_saved_review(db, review_id)
        assert restored.filters[0].selected is False
        assert restored.filters[0].severity.value == "low"


@pytest.mark.parametrize("change", ["filter", "membership", "time", "stale", "missing_token"])
def test_policy_draft_refusals_leave_base_unchanged(policy_review, change):
    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"])
    before = base_rows(accounts, review_id)
    from scanner.persistence.repository.review_policy import ReviewPolicyConflict

    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        data = draft(detail)
        if change == "filter":
            data.filters = []
        elif change == "membership":
            data.memberships = []
        elif change == "time":
            data.filters[0].start_seconds = 1.5
        elif change == "stale":
            data.policy_revision -= 1
        else:
            data.policy_revision = None
        with pytest.raises(ReviewPolicyConflict):
            repository.save_saved_review(db, review_id, data)
    assert base_rows(accounts, review_id) == before


def test_copy_uses_new_owner_not_source_overlay_and_transfer_invalidates_token(policy_review):
    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"])
    from scanner.db import SavedReviewRow
    from scanner.models import ReviewDuplicate
    from scanner.persistence.repository.review_policy import ReviewPolicyConflict
    from tests.multi_user_access_support import PEER_USER

    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        db.info["review_actor"] = ActivityOwner(PEER_USER, 1)
        copied = repository.duplicate_saved_review(
            db,
            review_id,
            ReviewDuplicate(
                revision=detail.revision,
                name="Peer copy",
                policy_revision=detail.policy_revision,
                policy_owner_id=detail.policy_owner_id,
            ),
        )
        assert copied.policy_owner_id == PEER_USER
        assert copied.filters[0].selected is False
        assert copied.filters[0].severity.value == "low"
        row = db.get(SavedReviewRow, review_id)
        row.owner_id = PEER_USER
        db.commit()
        moved = repository.get_saved_review(db, review_id)
        assert moved.policy_owner_id == PEER_USER
        assert moved.filters[0].selected is False
        with pytest.raises(ReviewPolicyConflict):
            repository.save_saved_review(db, review_id, draft(detail))
