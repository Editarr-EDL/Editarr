"""Policy evidence remains repairable without weakening the publication floor."""

import pytest

from scanner import repository
from scanner.db import MediaItemRow, ReviewFilterRow, SavedReviewRow, ScanRow
from scanner.models import ReviewRename, ReviewRetimeRequest, ReviewSynchronization
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_review_policy_view import base_rows, draft, enforce
from tests.test_review_policy_view import policy_review as policy_review


def test_filter_deleted_before_enforcement_locks_the_review_with_no_restore_path(policy_review):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        original = repository.get_saved_review(db, review_id)
        repository.save_saved_review(db, review_id, draft(original, filters=[]))
    before = base_rows(accounts, review_id)
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert detail.policy_missing_evidence
        assert not detail.filters
        for output in (repository.export_saved_review_edl, repository.export_saved_review_mcf):
            with pytest.raises(ReviewPolicyConflict) as refused:
                output(
                    db,
                    review_id,
                    revision=detail.revision,
                    policy_revision=detail.policy_revision,
                    policy_owner_id=detail.policy_owner_id,
                )
            assert refused.value.code == "review_policy_evidence_missing"
        renamed = repository.rename_saved_review(
            db, review_id, ReviewRename(revision=detail.revision, name="Repairable")
        )
        assert renamed.policy_missing_evidence
        saved = repository.save_saved_review(
            db,
            review_id,
            draft(renamed, filters=[value.model_dump() for value in original.filters]),
        )
        assert not saved.policy_missing_evidence
        assert saved.filters[0].selected
    assert before == []
    enforce(accounts, rules=[])
    with accounts.factory() as db:
        restored = repository.get_saved_review(db, review_id)
        assert not restored.filters[0].selected


def retime_and_save(accounts, review_id, monkeypatch, *, enforced, edit=None):
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 100)
    if enforced:
        enforce(accounts)
    with accounts.factory() as db:
        db.info["review_actor"] = ActivityOwner(USER, 1)
        db.get(MediaItemRow, "media").path = db.get(ScanRow, "scan").resolved_local_path
        db.commit()
        detail = repository.get_saved_review(db, review_id)
        data = draft(detail)
        if edit is None:
            result = repository.retime_saved_review_draft(
                db,
                review_id,
                ReviewRetimeRequest(
                    revision=detail.revision,
                    memberships=detail.memberships,
                    current_synchronization=detail.synchronization,
                    proposed_synchronization=ReviewSynchronization(
                        start_seconds=10, end_seconds=90, end_mode="before_credits"
                    ),
                    mcf_document=detail.mcf_document,
                    filters=data.filters,
                    accepted_credits=None,
                    new_manual_filters=[],
                    policy_owner_id=detail.policy_owner_id,
                    policy_revision=detail.policy_revision,
                ),
            )
            data.filters = result.filters
            data.synchronization = result.synchronization
            data.mcf_document = result.mcf_document
        else:
            data.filters[0] = data.filters[0].model_copy(update=edit)
        return repository.save_saved_review(db, review_id, data)


def test_retimed_required_cue_cannot_be_saved_under_policy(policy_review, monkeypatch):
    accounts, review_id = policy_review
    saved = retime_and_save(accounts, review_id, monkeypatch, enforced=True)
    assert saved.filters[0].start_seconds == pytest.approx(10.8)
    assert saved.filters[0].end_seconds == pytest.approx(11.6)


def test_retime_saves_without_policy(policy_review, monkeypatch):
    accounts, review_id = policy_review
    saved = retime_and_save(accounts, review_id, monkeypatch, enforced=False)
    assert saved.filters[0].start_seconds == pytest.approx(10.8)


def test_widening_a_required_cue_is_refused(policy_review, monkeypatch):
    accounts, review_id = policy_review
    saved = retime_and_save(
        accounts,
        review_id,
        monkeypatch,
        enforced=True,
        edit={"start_seconds": 0.5, "end_seconds": 3},
    )
    assert (saved.filters[0].start_seconds, saved.filters[0].end_seconds) == (0.5, 3)


@pytest.mark.parametrize("interval", [(1.5, 2), (1, 1.5), (5, 6)])
def test_narrowing_or_moving_required_cue_is_refused(policy_review, monkeypatch, interval):
    accounts, review_id = policy_review
    with pytest.raises(ReviewPolicyConflict) as refused:
        retime_and_save(
            accounts,
            review_id,
            monkeypatch,
            enforced=True,
            edit={"start_seconds": interval[0], "end_seconds": interval[1]},
        )
    assert refused.value.code == "review_policy_enforced"


def test_tokenless_old_owner_effective_view_cannot_become_new_owner_base(policy_review):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        old = repository.get_saved_review(db, review_id)
        assert old.filters[0].selected
        row = db.get(SavedReviewRow, review_id)
        row.owner_id = PEER_USER
        db.commit()
        data = draft(old, policy_owner_id=None, policy_revision=None)
        with pytest.raises(ReviewPolicyConflict) as refused:
            repository.save_saved_review(db, review_id, data)
        assert refused.value.code == "stale_review_policy"
        assert not db.query(ReviewFilterRow).filter_by(review_id=review_id).one().selected
