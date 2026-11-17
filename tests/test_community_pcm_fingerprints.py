import hashlib
import struct
import wave
from dataclasses import replace

import pytest

from scanner.formats.community.stream_fingerprints import PCM_AUDIO_ALGORITHM
from scanner.integrations.community import pcm_fingerprints
from scanner.modeling.community_matching import MatchEvidence, classify
from scanner.modeling.media_identity import (
    DurationEvidence,
    MediaIdentity,
    ProviderIdentifier,
    ReleaseEvidence,
)
from scanner.pipeline.audio import extract_audio
from scanner.pipeline.audio_tracks import probe_audio_tracks
from tests.test_stream_fingerprints import generated_streams as generated_streams


def test_lossless_formats_match_samples_but_delayed_track_needs_different_timestamps(
    generated_streams, tmp_path
):
    source, generate = generated_streams
    sample_count = 16_001
    samples = struct.pack(f"<{sample_count}h", *(i % 100 - 50 for i in range(sample_count)))
    wav = tmp_path / "original.wav"
    with wave.open(str(wav), "wb") as pcm:
        pcm.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        pcm.writeframes(samples)

    def fingerprint(name, codec, offset, provenance):
        path = generate(
            name,
            [
                "-i",
                str(source),
                "-itsoffset",
                offset,
                "-i",
                str(wav),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                codec,
            ],
        )
        selection = probe_audio_tracks(path).select(1)
        return pcm_fingerprints.fingerprint_pcm_audio(path, selection, provenance=provenance)

    original = fingerprint("pcm.mkv", "pcm_s16le", "0", "analyzed")
    lossless = fingerprint("flac.mkv", "flac", "0", "selected")
    delayed = fingerprint("delayed.mkv", "flac", "0.25", "selected")
    digest = hashlib.sha256(PCM_AUDIO_ALGORITHM.encode() + b"\0" + samples).hexdigest()
    assert original.digest == lossless.digest == delayed.digest == digest
    assert original.duration == lossless.duration == delayed.duration == "16001/16000"
    assert original.playback_start == lossless.playback_start == "0"
    assert delayed.playback_start == "1/4"

    media = MediaIdentity(
        "movie",
        (ProviderIdentifier("tmdb", "movie", "123", "user_asserted"),),
        release=ReleaseEvidence(duration=DurationEvidence(3, "file_probe")),
    )
    candidate = MatchEvidence(media, audio_stream=original)
    target = MatchEvidence(media, audio_stream=lossless)
    assert classify(target, candidate).audio.strong_eligible
    result = classify(replace(target, audio_stream=delayed), candidate).audio
    assert result.conflicts == ("audio_stream_playback_start",)
    assert not result.strong_eligible


def test_eac3_recipient_matches_scanner_wav_for_the_selected_track(
    generated_streams, tmp_path, monkeypatch
):
    source, generate = generated_streams
    original = generate(
        "dual.mkv", ["-i", str(source), "-map", "0", "-c:v", "copy", "-c:a", "eac3"]
    )
    recipient = generate("remux.mkv", ["-i", str(original), "-map", "0", "-c", "copy"])
    first = probe_audio_tracks(original).select(2)
    second = probe_audio_tracks(recipient).select(2)
    wrong = probe_audio_tracks(recipient).select(1)
    assert first.track.codec == second.track.codec == "eac3"
    wav = tmp_path / "scanned.wav"
    extract_audio(str(original), str(wav), audio_selection=first)
    monkeypatch.setattr(
        pcm_fingerprints,
        "cached_audio_path",
        lambda key: wav if key == first.extraction_cache_key() else tmp_path / "missing.wav",
    )
    with monkeypatch.context() as capture:
        capture.setattr(
            pcm_fingerprints,
            "run_command",
            lambda *_args, **_kwargs: pytest.fail("the analyzed WAV was already available"),
        )
        analyzed = pcm_fingerprints.fingerprint_pcm_audio(original, first, provenance="analyzed")
    matched = pcm_fingerprints.fingerprint_pcm_audio(recipient, second, provenance="selected")
    unrelated = pcm_fingerprints.fingerprint_pcm_audio(recipient, wrong, provenance="selected")
    assert analyzed == replace(matched, provenance="analyzed")
    assert matched.digest != unrelated.digest
