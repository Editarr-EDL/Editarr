"""Subtitle redaction applies one rule to every audio language filter, whatever its origin."""

from types import SimpleNamespace

import pytest

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.formats.subtitle_redaction import SubtitleMute
from scanner.modeling.model_review_filters import ReviewFilter
from scanner.persistence.repository.review_subtitles import subtitle_mutes

ORIGINS = {
    "scan": {"source_scan_id": "scan-1", "source_finding_id": 1},
    "manual": {"source_scan_id": "scan-1"},
    "community": {"community_source_id": "source-1", "community_filter_id": "f_1"},
}


def language_filter(origin, **changes):
    values = {
        "source": "audio",
        "category": "language",
        "subcategory": "swearing",
        "mcf_channel": "audio",
        "label": "damn",
        "details": None,
        "group_key": None,
        "start_seconds": 10,
        "end_seconds": 12,
        "confidence": None,
        "edit_action": "mute",
        "selected": True,
    }
    return ReviewFilter(id="filter", **ORIGINS[origin], **{**values, **changes})


def mutes(*filters):
    return subtitle_mutes(SimpleNamespace(filters=list(filters), required_ids=set()), 120)


@pytest.mark.parametrize("origin", sorted(ORIGINS))
@pytest.mark.parametrize("action", ["mute", "skip"])
def test_language_word_is_redacted_whatever_the_filter_action(origin, action):
    # A skip never leaves its word readable in a cue that is shown around the range.
    assert mutes(language_filter(origin, edit_action=action)) == (
        SubtitleMute(10.0, 12.0, "damn"),
    )


@pytest.mark.parametrize("origin", sorted(ORIGINS))
@pytest.mark.parametrize(
    "change",
    [
        {"selected": False},
        {"category": "violence", "subcategory": "violence"},
        {"mcf_channel": "video"},
        {"label": None},
    ],
)
def test_unselected_non_language_video_and_wordless_filters_redact_nothing(origin, change):
    assert mutes(language_filter(origin, edit_action="skip", **change)) == ()
