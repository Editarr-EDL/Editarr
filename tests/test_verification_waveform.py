"""Recover transcript gaps only where the same audio can be located directly."""

import wave
from types import SimpleNamespace

import numpy as np

from scanner.modeling.cue_alignment import AudioCue
from scanner.modeling.model_version_reuse import ReuseAnchor
from scanner.modeling.version_alignment import Alignment
from scanner.persistence.repository.version_reuse import project_cues
from scanner.runtime import verification_waveform


def test_cached_audio_recovers_missing_mute_but_leaves_unmatched_cues_pending(
    tmp_path, monkeypatch
):
    sample_rate = 16_000
    shift = int(0.4 * sample_rate)
    rng = np.random.default_rng(7)
    source = rng.integers(-4000, 4000, sample_rate * 24, dtype=np.int16)
    target = np.concatenate((np.zeros(shift, dtype=np.int16), source))
    target[14 * sample_rate : 20 * sample_rate] = 0
    for name, samples in [("original", source), ("target", target)]:
        with wave.open(str(tmp_path / name), "wb") as output:
            output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            output.writeframes(samples.astype("<i2").tobytes())

    def track(name):
        return SimpleNamespace(extraction_cache_key=lambda: name)

    monkeypatch.setattr(
        verification_waveform, "cached_audio_path", lambda identity, *, create: tmp_path / identity
    )
    alignment = Alignment.from_anchors(
        [
            ReuseAnchor(source_seconds=5, target_seconds=5.3),
            ReuseAnchor(source_seconds=6, target_seconds=6.3),
        ]
    )
    cues = [AudioCue(10, 10.5), AudioCue(17, 17.5)]
    matches = verification_waveform.match_cached_audio_cues(
        cues, alignment, track("original"), track("target"), 0, 0
    )
    assert matches == {(10, 10.5): (10.4, 10.9)}
    findings = [
        SimpleNamespace(
            id=index,
            source="audio",
            mcf_channel=channel,
            start_seconds=cue.start,
            end_seconds=cue.end,
        )
        for index, (channel, cue) in enumerate(
            [("audio", cues[0]), ("audio", cues[1]), ("video", cues[0])]
        )
    ]
    projection = project_cues(
        findings, ["audio", "video"], Alignment(alignment.segments, matches), 24
    )
    assert [(cue.target_start, cue.target_end) for cue in projection] == [
        (10.4, 10.9),
        (None, None),
        (None, None),
    ]


def test_repeated_audio_has_no_unique_match():
    rng = np.random.default_rng(9)
    original = rng.normal(0, 1000, 2 * 16_000)
    assert verification_waveform._match(original.copy(), np.tile(original, 3)) is None
