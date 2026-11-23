"""Fallback confidence for scan dialogue, external notes, and timing-only evidence."""

import pytest

from scanner.modeling.cue_alignment import (
    AudioCue,
    cue_anchors,
    infer_transcript_offset,
    match_unmapped_dialogue,
)
from scanner.modeling.version_alignment import Alignment, TimedWord

_FIRST = "please turn the silver lantern down tonight"
_SECOND = "the velvet ribbon belongs inside this drawer"


def _phrase(text: str, start: float) -> list[TimedWord]:
    return [
        TimedWord(word, start + index * 0.25, start + index * 0.25 + 0.2)
        for index, word in enumerate(text.split())
    ]


def _cue(midpoint: float, label: str = "", dialogue: str = "") -> AudioCue:
    return AudioCue(midpoint - 0.25, midpoint + 0.25, label, dialogue)


def test_two_distinct_dialogue_landmarks_work_without_source_audio():
    original = [
        _cue(98.10, "lantern", _FIRST),
        _cue(140, "unverified"),
        _cue(195.60, "ribbon", _SECOND),
    ]
    target = _phrase(_FIRST, 100) + _phrase(_SECOND, 198)
    anchors, method = cue_anchors(original, target, [], 400)
    assert method == "dialogue"
    alignment = Alignment.from_anchors(anchors)
    assert alignment.project(original[0].start, original[0].end) == pytest.approx((100.85, 101.35))
    assert alignment.project(original[1].start, original[1].end) is None
    assert alignment.project(original[2].start, original[2].end) == pytest.approx((198.35, 198.85))


def test_community_note_can_anchor_a_unique_target_mute_without_a_spoken_word_label():
    original = [_cue(99, "language", _FIRST), _cue(198, "language", _SECOND)]
    words = _phrase(_FIRST, 100) + _phrase(_SECOND, 199)
    anchors, method = cue_anchors(original, words, [_cue(101), _cue(200)], 400)
    assert method == "dialogue"
    assert Alignment.from_anchors(anchors).project(98.75, 99.25) == (100.75, 101.25)


def test_repeated_dialogue_and_insufficient_reference_timing_require_manual_alignment():
    original = [_cue(98.1, "lantern", _FIRST), _cue(198.1, "ribbon", _SECOND)]
    target = _phrase(_FIRST, 100) + _phrase(_FIRST, 120) + _phrase(_SECOND, 200)
    assert cue_anchors(original, target, [_cue(101), _cue(200)], 400) == ([], None)


def test_timing_only_import_requires_one_distinctive_pattern():
    original = [_cue(time) for time in (100, 114, 147, 184, 232, 300)]
    destination = [_cue(time + 9.5) for time in (100, 114, 147, 184, 232, 300)]
    destination += [_cue(80), _cue(174), _cue(209)]
    anchors, method = cue_anchors(original, [], destination, 400)
    assert method == "timing"
    assert Alignment.from_anchors(anchors).project(99.75, 100.25) == (109.25, 109.75)

    recurring = [_cue(time) for time in (90, 110, 130, 150)]
    ambiguous = [_cue(time) for time in (100, 120, 140, 160, 180)]
    assert cue_anchors(recurring, [], ambiguous, 400) == ([], None)
    assert cue_anchors(original[:2], [], destination[:2], 400) == ([], None)


def test_shared_verification_uses_target_transcript_for_downloads(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from scanner.http import reuse_alignment

    original = SimpleNamespace(id="download", scan_audio=True, audio_model=None)
    target_scan = SimpleNamespace(id="transcribed", audio_model="whisper")
    centers = (100, 114, 147, 184, 232, 300)
    flags = {
        original.id: [_cue(time) for time in centers],
        target_scan.id: [_cue(time + 9.5) for time in centers],
    }

    def findings(_db, scan_id):
        return [
            SimpleNamespace(
                start_seconds=cue.start,
                end_seconds=cue.end,
                label=cue.label,
                details=cue.dialogue,
                source="audio",
                mcf_channel="audio",
                edit_action="mute",
            )
            for cue in flags[scan_id]
        ]

    monkeypatch.setattr(reuse_alignment, "_audio_reference", lambda *_: None)
    monkeypatch.setattr(reuse_alignment, "_target_audio_scans", lambda *_: [target_scan])
    monkeypatch.setattr(reuse_alignment, "source_findings", findings)
    monkeypatch.setattr(reuse_alignment, "target_transcript_words", lambda *_: _phrase(_FIRST, 109))
    context = (SimpleNamespace(db=None), original, None, None, tmp_path, None, 400)
    alignment, messages, scan_id = reuse_alignment.align_from_transcript(*context)
    assert alignment.project(99.75, 100.25) == (109.25, 109.75)
    assert scan_id == target_scan.id
    assert "flagged audio times" in messages[0]

    flags[original.id] = flags[original.id][:2]
    alignment, messages, scan_id = reuse_alignment.align_from_transcript(*context)
    assert alignment.anchors == [] and scan_id is None
    assert "enough confidence" in messages[0]
    assert "manually" in messages[0]


def test_local_match_uses_saved_context_for_cue_outside_matched_intervals():
    first = _cue(98.1, "lantern", _FIRST)
    second = _cue(195.6, "ribbon", _SECOND)
    middle = _cue(147.1, "copper", "open that copper gate tomorrow please")
    words = (
        _phrase(_FIRST, 100)
        + _phrase("open that copper window today", 149.6)
        + _phrase(_SECOND, 198)
    )
    anchors, _ = cue_anchors([first, second], words, [], 400)
    alignment = Alignment.from_anchors(anchors)
    assert alignment.project(middle.start, middle.end) is None
    assert match_unmapped_dialogue([middle], words, alignment) == {
        (middle.start, middle.end): (149.95, 150.45)
    }
    assert (
        match_unmapped_dialogue(
            [_cue(147.1, "copper", "shine the copper coins now")], words, alignment
        )
        == {}
    )


def test_legacy_clock_uses_repeated_matching_flags_on_the_same_track():
    third = "the signal light flickered four times before dawn"
    original = [
        _cue(105.1, "lantern", _FIRST),
        _cue(204.6, "ribbon", _SECOND),
        _cue(304.85, "flickered", third),
    ]
    words = _phrase(_FIRST, 100) + _phrase(_SECOND, 200) + _phrase(third, 300)
    assert infer_transcript_offset(original, words) == pytest.approx(4.0)
    assert infer_transcript_offset(original[:2], words) is None
