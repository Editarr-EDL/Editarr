import hashlib
import json
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from scanner.formats.community.stream_fingerprints import (
    AUDIO_ALGORITHM,
    VIDEO_ALGORITHM,
    stream_digest,
)
from scanner.integrations.community.stream_experiments import BoundedInput, StreamUnsupported


def test_independent_node_vectors():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/community/stream-fingerprints-v1.json").read_text()
    )
    for vector in fixture["vectors"]:
        assert (
            stream_digest(
                vector["channel"],
                vector["codec"],
                bytes.fromhex(fixture["configuration_hex"]),
                [[bytes.fromhex(packet) for packet in sample] for sample in fixture["samples_hex"]],
            )
            == vector["digest"]
        )


@pytest.fixture
def generated_streams(tmp_path):
    from scanner.runtime.runtime_support import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()

    def generate(name, arguments):
        output = tmp_path / name
        subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", *arguments, str(output)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return output

    base = generate(
        "base.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-g",
            "30",
            "-c:a",
            "aac",
        ],
    )
    return base, generate


def test_generated_remux_reorder_and_independent_channel_changes(generated_streams):
    from scanner.integrations.community.stream_fingerprints import fingerprint_stream

    base, generate = generated_streams
    originals = [fingerprint_stream(base, index) for index in range(3)]
    remux = generate("remux.mov", ["-i", str(base), "-map", "0", "-c", "copy"])
    assert [fingerprint_stream(remux, index).digest for index in range(3)] == [
        item.digest for item in originals
    ]
    reordered = generate(
        "reordered.mp4",
        ["-i", str(base), "-map", "0:v", "-map", "0:a:1", "-map", "0:a:0", "-c", "copy"],
    )
    assert [fingerprint_stream(reordered, index).digest for index in range(3)] == [
        originals[index].digest for index in (0, 2, 1)
    ]
    changed_audio = generate(
        "changed-audio.mp4",
        [
            "-i",
            str(base),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=3",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "0:a:1",
            "-c:v",
            "copy",
            "-c:a:0",
            "aac",
            "-c:a:1",
            "copy",
        ],
    )
    assert fingerprint_stream(changed_audio, 0).digest == originals[0].digest
    assert fingerprint_stream(changed_audio, 1).digest != originals[1].digest
    assert fingerprint_stream(changed_audio, 2).digest == originals[2].digest
    changed_video = generate(
        "changed-video.mp4",
        [
            "-i",
            str(base),
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=160x90:rate=30:duration=3",
            "-map",
            "1:v",
            "-map",
            "0:a",
            "-c:v",
            "libx264",
            "-g",
            "30",
            "-c:a",
            "copy",
        ],
    )
    assert fingerprint_stream(changed_video, 0).digest != originals[0].digest
    assert [fingerprint_stream(changed_video, index).digest for index in (1, 2)] == [
        originals[index].digest for index in (1, 2)
    ]
    assert all(
        item.bytes_read <= 16 * 1024 * 1024 and item.payload_bytes <= 3 * 1024 * 1024
        for item in originals
    )
    from scanner.modeling.community_matching import MatchEvidence, StreamEvidence, classify
    from scanner.modeling.media_identity import (
        AudioEvidence,
        DurationEvidence,
        MediaIdentity,
        ProviderIdentifier,
        ReleaseEvidence,
    )

    media = MediaIdentity(
        "movie",
        (ProviderIdentifier("tmdb", "movie", "1", "synthetic"),),
        release=ReleaseEvidence(
            duration=DurationEvidence(3, "file_probe"), audio=AudioEvidence("en", "main")
        ),
    )

    def comparison_input(path):
        video, audio = fingerprint_stream(path, 0), fingerprint_stream(path, 1)
        return MatchEvidence(
            media,
            video_stream=StreamEvidence(
                video.algorithm, video.digest, video.playback_start, video.duration
            ),
            audio_stream=StreamEvidence(
                audio.algorithm, audio.digest, audio.playback_start, audio.duration, "analyzed"
            ),
        )

    target = comparison_input(base)
    result = classify(target, comparison_input(remux))
    assert result.video.band == result.audio.band == "strong_fingerprint"
    result = classify(target, comparison_input(changed_audio))
    assert result.video.band == "strong_fingerprint" and not result.audio.strong_eligible
    result = classify(target, comparison_input(changed_video))
    assert result.audio.band == "strong_fingerprint" and not result.video.strong_eligible


def test_cache_checks_mutation_and_cancellation(generated_streams, monkeypatch):
    from scanner.integrations.community import stream_fingerprints as adapter

    base, _ = generated_streams
    original = adapter.fingerprint_stream(base, 1)
    monkeypatch.setattr(
        adapter, "sample_stream", lambda *_args, **_kwargs: pytest.fail("cache was not used")
    )
    cached = adapter.fingerprint_stream(base, 1)
    assert cached.digest == original.digest and cached.cache_hit
    assert cached.bytes_read == cached.payload_bytes == cached.packets_seen == cached.seeks == 0
    with pytest.raises(StreamUnsupported, match="cancelled"):
        adapter.fingerprint_stream(base, 1, should_cancel=lambda: True)
    with base.open("ab") as source:
        source.write(b"changed-file-version")
    with pytest.raises(BaseException, match="cache was not used"):
        adapter.fingerprint_stream(base, 1)


def test_exact_timing_is_relative_to_container_playback_origin(generated_streams):
    import av

    from scanner.integrations.community.stream_fingerprints import fingerprint_stream

    base, generate = generated_streams
    shifted = generate(
        "shifted.mp4",
        ["-i", str(base), "-map", "0", "-c", "copy", "-output_ts_offset", "3"],
    )
    for path in (base, shifted):
        with av.open(str(path)) as container:
            assert container.start_time is not None
            if path == shifted:
                assert container.start_time > 0
            for stream in container.streams:
                result = fingerprint_stream(path, stream.index)
                expected_start = Fraction(stream.start_time * stream.time_base) - Fraction(
                    container.start_time, av.time_base
                )
                assert Fraction(result.playback_start) == expected_start
                assert Fraction(result.duration) == stream.duration * stream.time_base
                assert result.start_seconds == float(stream.start_time * stream.time_base)


@pytest.mark.parametrize(
    "channel,codec,algorithm",
    [("audio", "aac", AUDIO_ALGORITHM), ("video", "h264", VIDEO_ALGORITHM)],
)
def test_stream_framing_is_explicit_and_domain_separated(channel, codec, algorithm):
    configuration = b"config"
    samples = [[b"first"], [b"second", b"packet"], [b"last"]]

    def frame(payload):
        return struct.pack(">I", len(payload)) + payload

    encoded = (
        algorithm.encode()
        + b"\0"
        + frame(codec.encode())
        + frame(configuration)
        + struct.pack(">I", 3)
    )
    for sample in samples:
        encoded += struct.pack(">I", len(sample)) + b"".join(frame(packet) for packet in sample)
    assert (
        stream_digest(channel, codec, configuration, samples) == hashlib.sha256(encoded).hexdigest()
    )
    assert stream_digest(channel, codec, configuration, samples) != stream_digest(
        channel, codec, configuration, list(reversed(samples))
    )


def test_stream_hash_rejects_unsupported_empty_and_unbounded_inputs():
    for channel, codec, configuration, samples in [
        ("audio", "pcm_s16le", b"config", [[b"x"]] * 3),
        ("audio", "aac", b"", [[b"x"]] * 3),
        ("audio", "aac", b"config", [[b"x"]] * 2),
        ("audio", "aac", b"config", [[], [b"x"], [b"x"]]),
        ("audio", "aac", b"config", [[b"x" * (3 * 1024 * 1024)], [b"x"], [b"x"]]),
    ]:
        with pytest.raises(ValueError):
            stream_digest(channel, codec, configuration, samples)


def test_counting_input_enforces_read_seek_and_cancellation_budgets(tmp_path):
    path = tmp_path / "generated"
    path.write_bytes(b"synthetic")
    source = BoundedInput(path)
    try:
        assert source.read(3) == b"syn"
        assert source.bytes_read == 3 and source.read_calls == 1
        assert source.seek(0) == 0
        assert source.read(9) == b"synthetic"
        assert source.bytes_read == 12
        with pytest.raises(StreamUnsupported, match="read_budget"):
            source.read(16 * 1024 * 1024)
        source.seeks = 128
        with pytest.raises(StreamUnsupported, match="seek_budget"):
            source.seek(0)
        source.should_cancel = lambda: True
        with pytest.raises(StreamUnsupported, match="cancelled"):
            source.read(1)
        source.should_cancel = None
        source.started -= 11
        with pytest.raises(StreamUnsupported, match="wall_budget"):
            source.check()
    finally:
        source.source.close()


def test_sampler_rejects_inflight_mutation_missing_stream_and_unsupported_codec(
    generated_streams, monkeypatch
):
    from scanner.integrations.community.stream_experiments import SamplingCandidate, sample_stream

    base, generate = generated_streams
    candidate = SamplingCandidate(3, 0.25)
    with pytest.raises(StreamUnsupported, match="missing_stream"):
        sample_stream(base, 127, candidate)
    unsupported = generate(
        "unsupported.mov", ["-i", str(base), "-map", "0:a:0", "-c:a", "pcm_s16le"]
    )
    with pytest.raises(StreamUnsupported, match="unsupported_codec_configuration"):
        sample_stream(unsupported, 0, candidate)
    actual_check = BoundedInput.check
    checks = []

    def mutate_during_sampling(source):
        actual_check(source)
        checks.append(True)
        if len(checks) == 3:
            with base.open("ab") as output:
                output.write(b"mutation")

    monkeypatch.setattr(BoundedInput, "check", mutate_during_sampling)
    with pytest.raises(StreamUnsupported, match="file_changed"):
        sample_stream(base, 1, candidate)


def test_actual_audio_and_video_entry_points_use_only_one_counted_probe(
    generated_streams, monkeypatch
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from scanner.db import Base, ScanRow
    from scanner.integrations.community import stream_experiments
    from scanner.integrations.community.stream_fingerprints import (
        fingerprint_analyzed_audio,
        fingerprint_stream,
        fingerprint_video,
    )
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.pipeline.audio_tracks import AudioSelectionError, probe_audio_tracks

    base, _ = generated_streams
    selected = AudioSelection.from_selection(probe_audio_tracks(base).select(2))
    expected = fingerprint_stream(base, 2).digest
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    opened = []
    actual_open = stream_experiments.av.open

    def counted_open(source, **kwargs):
        assert isinstance(source, BoundedInput)
        opened.append(source)
        return actual_open(source, **kwargs)

    monkeypatch.setattr(stream_experiments.av, "open", counted_open)
    try:
        with Session(engine) as db:
            row = ScanRow(
                id="actual",
                media_item_id="synthetic",
                scan_number=1,
                source_path=str(base),
                resolved_local_path=str(base),
                scan_audio=True,
                scan_video=False,
                origin="generated",
                requested_audio_selection_json=selected.model_dump_json(),
            )
            db.add(row)
            db.commit()
            with pytest.raises(StreamUnsupported, match="analyzed_audio_unknown"):
                fingerprint_analyzed_audio(db, row.id)
            assert not opened
            row.analyzed_audio_selection_json = selected.model_dump_json()
            db.commit()
            result = fingerprint_analyzed_audio(db, row.id)
            assert result.digest == expected and len(opened) == 1
            assert result.bytes_read == opened[0].bytes_read
            assert fingerprint_analyzed_audio(db, row.id).cache_hit and len(opened) == 1
            fingerprint_video(base)
            assert len(opened) == 2
            row.origin = "imported"
            db.commit()
            with pytest.raises(StreamUnsupported, match="analyzed_audio_unknown"):
                fingerprint_analyzed_audio(db, row.id)
            row.origin = "generated"
            db.commit()
            with base.open("ab") as source:
                source.write(b"mutated")
            with pytest.raises(AudioSelectionError, match="file changed"):
                fingerprint_analyzed_audio(db, row.id)
    finally:
        engine.dispose()
