"""Community language mutes reach subtitles only through an aligned local audio finding."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.db import (
    MediaVersionRow,
    ReviewFilterRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMediaVersionRow,
    ScanRow,
)
from scanner.formats.subtitle_redaction import redact_subtitles
from scanner.formats.subtitles import parse_subtitles
from scanner.persistence.repository.repository_review_core import get_saved_review
from scanner.persistence.repository.review_community_subtitles import community_subtitle_mutes
from tests.community_master_http_support import VERSION_ID
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import USER
from tests.test_review_community_sources import activities, include, masters, review, save, session

__all__ = ["activities", "masters", "review"]

SWEAR = served_filter(
    "f_swear", 39_000, 42_000, category="language", subcategory="swearing", action="mute"
)
SUBTITLES = b"1\r\n00:00:39,000 --> 00:00:42,000\r\nWell damn it.\r\n"


def _audio_word(masters, start, end, word, *, version=None):
    """Make the seeded scan a completed audio scan with one word-level language finding,
    optionally bound to a local media version; returns the finding ID."""
    with masters.app.accounts.factory() as db:
        db.get(ScanRow, masters.scan_id).scan_audio = True
        if version is not None:
            if db.get(MediaVersionRow, version) is None:
                db.add(MediaVersionRow(id=version, title_id="title-http"))
                db.flush()
            db.merge(ScanMediaVersionRow(scan_id=masters.scan_id, version_id=version))
        finding = ScanFindingRow(
            scan_id=masters.scan_id,
            category="language",
            subcategory="swearing",
            start_seconds=start,
            end_seconds=end,
            label=word,
            source="audio",
            edit_action="mute",
        )
        db.add(finding)
        db.commit()
        return finding.id


def _mutes(masters, review):
    with session(masters) as db:
        result = community_subtitle_mutes(db, db.get(SavedReviewRow, review), None)
        assert get_saved_review(db, review).community_subtitles == result.summary
    return result


def _redacted(mutes) -> bytes:
    redaction = redact_subtitles(parse_subtitles(SUBTITLES, "srt"), tuple(mutes))
    return redaction.document.render(redaction.replacements)


@pytest.mark.parametrize(
    ("word", "applied"), [((40.0, 40.5), True), ((80.0, 80.5), False), (None, False)]
)
def test_only_an_aligned_local_word_reaches_subtitles(masters, review, word, applied):
    link = masters.link(USER, "v_1", "2", SWEAR)
    save(masters, review, include(link["linkId"]))
    if word is not None:
        _audio_word(masters, *word, "damn")
    result = _mutes(masters, review)
    expected = {"applied": 0, "needs_audio_scan": 0, "unaligned": 0}
    expected["applied" if applied else "unaligned" if word else "needs_audio_scan"] = 1
    assert result.summary.model_dump() == expected
    assert (b"damn" not in _redacted(result.mutes)) is applied
    assert [mute.expression for mute in result.mutes] == (["damn"] if applied else [])


@pytest.mark.parametrize(("version", "applied"), [(VERSION_ID, True), ("mv_other", False)])
def test_a_scan_bound_to_another_version_is_not_local_evidence(masters, review, version, applied):
    link = masters.link(USER, "v_1", "2", SWEAR)
    save(masters, review, include(link["linkId"]))
    _audio_word(masters, 40.0, 40.5, "damn", version=version)
    summary = _mutes(masters, review).summary
    assert (summary.applied, summary.needs_audio_scan) == ((1, 0) if applied else (0, 1))


def test_a_scan_mute_and_a_community_mute_redact_the_same_word_once(masters, review):
    from scanner.modeling.model_subtitles import SubtitleSourcesRequest
    from scanner.persistence.repository.review_subtitles import review_subtitle_evidence

    link = masters.link(USER, "v_1", "2", SWEAR)
    finding = _audio_word(masters, 40.0, 40.5, "damn")
    with masters.app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review)
        row.memberships[0].role = "both"
        db.add(
            ReviewFilterRow(
                review_id=review,
                id=str(uuid4()),
                source_scan_id=masters.scan_id,
                source_finding_id=finding,
                position=0,
                source="audio",
                category="language",
                subcategory="swearing",
                label="damn",
                start_seconds=40.0,
                end_seconds=40.5,
                edit_action="mute",
                selected=True,
            )
        )
        db.commit()
    saved = save(masters, review, include(link["linkId"]))
    # Both the review's own scan mute and the community mute name the same finding.
    assert [(f.label, f.edit_action.value, f.selected) for f in saved.filters] == [
        ("damn", "mute", True)
    ]
    assert saved.community_subtitles.applied == 1
    source = SimpleNamespace(
        media_source_id=None, duration_seconds=120.0, catalog_item=SimpleNamespace(versions=[])
    )
    target = SimpleNamespace(media=SimpleNamespace(resolved_path=masters.path), source=source)
    with session(masters) as db:
        evidence = review_subtitle_evidence(
            db,
            review,
            target,
            SubtitleSourcesRequest(
                revision=saved.revision,
                policy_revision=saved.policy_revision,
                policy_owner_id=saved.policy_owner_id,
            ),
        )
    assert [(m.start, m.end, m.expression) for m in evidence.mutes] == [(40.0, 40.5, "damn")]
    assert _redacted(evidence.mutes) == (b"1\r\n00:00:39,000 --> 00:00:42,000\r\nWell it.\r\n")
