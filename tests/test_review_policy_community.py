"""Community preparation binds effective content to current-owner policy evidence."""

from scanner.db import SavedReviewRow
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.donation_sources import SourceRef, capture_source
from scanner.integrations.community.submission_projection import project_submission
from scanner.persistence.repository.review_policy import prepare_review_policy
from tests.test_community_submission_projection import options
from tests.test_review_policy_view import base_rows, enforce
from tests.test_review_policy_view import policy_review as policy_review

BOUNDS = ResourceBounds(65536, 524288, 100, 4096, 24)


def test_community_capture_uses_same_effective_filters_and_changes_guard(policy_review):
    accounts, review_id = policy_review
    source = SourceRef("review", review_id)
    original = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    before = base_rows(accounts, review_id)
    enforce(accounts, severities=["swearing"])
    captured = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    data = captured.data()
    assert captured.guard != original.guard
    assert data["base_findings"][0]["selected"] is False
    assert data["findings"][0]["selected"] is True
    assert data["findings"][0]["severity"] == "high"
    with accounts.factory() as db:
        prepared = prepare_review_policy(db, db.get(SavedReviewRow, review_id))
        assert data["policy"] == {
            "owner_id": prepared.owner_id,
            "revision": prepared.policy_revision,
        }
        assert data["findings"][0]["severity"] == prepared.filters[0].severity.value
    assert base_rows(accounts, review_id) == before
    enforce(accounts, rules=[])
    restored = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    assert restored.guard != captured.guard
    assert restored.data()["findings"][0]["selected"] is False


def test_submission_projection_uses_effective_severity(policy_review):
    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"])
    captured = capture_source(
        accounts.engine, SourceRef("review", review_id), bounds=BOUNDS, source_limit=4
    )
    projection = project_submission(captured.data(), "review", options(duration_ms=100_000), None)
    (entry,) = projection.artifact.entries
    assert (entry.severity, entry.start_ms) == ("high", 1000)
    enforce(accounts, rules=[])
    restored = capture_source(
        accounts.engine, SourceRef("review", review_id), bounds=BOUNDS, source_limit=4
    )
    assert restored.guard != captured.guard
