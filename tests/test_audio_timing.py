"""Playback timing for streams beyond the metadata probe's read budget."""

import subprocess

import pytest

from scanner.pipeline.audio import extract_audio
from scanner.pipeline.audio_timing import audio_playback_offset
from scanner.pipeline.audio_tracks import probe_audio_tracks
from scanner.pipeline.bounded_media import BoundedInput, MediaBudgetError
from scanner.pipeline.processes import ScanCancelledError
from scanner.runtime.runtime_support import resolve_ffmpeg


@pytest.fixture
def delayed_media(tmp_path):
    source = tmp_path / "delayed.mkv"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-itsoffset",
            "7",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "rawvideo",
            "-allow_raw_vfw",
            "1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert source.stat().st_size > 16 * 1024 * 1024
    return source, probe_audio_tracks(source).select(2)


def test_timing_reaches_delayed_audio_beyond_probe_budget(delayed_media, tmp_path):
    source, selection = delayed_media
    extract_audio(str(source), str(tmp_path / "selected.wav"), audio_selection=selection)

    assert audio_playback_offset(str(source), selection) == pytest.approx(7, abs=0.001)


def test_timing_remains_cancellable_while_reading(delayed_media):
    source, selection = delayed_media
    checks = 0

    def cancel_during_read():
        nonlocal checks
        checks += 1
        return checks >= 40

    with pytest.raises(ScanCancelledError, match="Scan cancelled"):
        audio_playback_offset(str(source), selection, should_cancel=cancel_during_read)
    assert checks >= 40


def test_metadata_probes_keep_default_read_limit(tmp_path):
    source = tmp_path / "large-media"
    with source.open("wb") as writer:
        writer.truncate(17 * 1024 * 1024)
    with BoundedInput(source) as stream:
        for _ in range(16):
            assert len(stream.read(1024 * 1024)) == 1024 * 1024
        with pytest.raises(MediaBudgetError, match="read_budget"):
            stream.read(1)
