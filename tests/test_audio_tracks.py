import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.pipeline.audio_tracks import (
    MAX_STREAMS,
    PROBE_OPTIONS,
    AudioSelectionError,
    AudioTrack,
    AudioTrackInventory,
    probe_audio_tracks,
)


def track(index=1, *, title=None, disposition=0):
    return AudioTrack(index, "eng", title, "aac", 2, disposition, 0.0, 5.0, "1/48000")


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        ((), None),
        ((track(),), 1),
        ((track(1), track(2)), None),
        ((track(1), track(2, disposition=1)), 2),
        ((track(1, disposition=1), track(2, disposition=1)), None),
    ],
)
def test_preselection_is_only_track_or_unambiguous_default(tracks, expected):
    assert AudioTrackInventory("file-v1", tracks).preselected_stream_index == expected


def test_duplicate_languages_keep_distinct_titles_and_require_choice():
    inventory = AudioTrackInventory(
        "file-v1", (track(1, title="Main"), track(3, title="Commentary"))
    )
    with pytest.raises(AudioSelectionError, match="Choose"):
        inventory.select()
    chosen = inventory.select(3)
    assert chosen.track.language == "eng"
    assert chosen.track.title == "Commentary"
    assert inventory.revalidate(chosen) == chosen.track


@pytest.mark.parametrize("index", [True, "1", 2, -1])
def test_invalid_or_missing_stream_never_falls_back(index):
    with pytest.raises(AudioSelectionError):
        AudioTrackInventory("file-v1", (track(),)).select(index)


def test_no_audio_is_explicit():
    with pytest.raises(AudioSelectionError, match="No audio"):
        AudioTrackInventory("file-v1", ()).select()


def test_changed_file_or_track_never_reuses_selection():
    original = AudioTrackInventory("file-v1", (track(),))
    selection = original.select()
    for changed in (
        replace(original, file_version="file-v2"),
        replace(original, tracks=(track(title="Commentary"),)),
        replace(original, tracks=(track(2, disposition=1),)),
        replace(original, tracks=()),
    ):
        with pytest.raises(AudioSelectionError):
            changed.revalidate(selection)


def test_cache_identity_binds_track_file_and_extraction_behavior():
    inventory = AudioTrackInventory("file-v1", (track(1), track(2)))
    selection = inventory.select(1)
    assert selection.extraction_cache_key() == inventory.select(1).extraction_cache_key()
    assert selection.extraction_cache_key() != inventory.select(2).extraction_cache_key()
    assert (
        selection.extraction_cache_key()
        != replace(selection, file_version="file-v2").extraction_cache_key()
    )


@pytest.mark.parametrize("mutation", ["none", "before", "after"])
def test_exact_extraction_revalidates_before_publishing(tmp_path, monkeypatch, mutation):
    from scanner.pipeline import audio

    inventory = AudioTrackInventory("file-v1", (track(1), track(3)))
    selection = inventory.select(3)
    changed = replace(inventory, file_version="file-v2")
    probes = iter(
        [
            changed if mutation == "before" else inventory,
            changed if mutation == "after" else inventory,
        ]
    )
    monkeypatch.setattr(audio, "probe_audio_tracks", lambda _path: next(probes))
    monkeypatch.setattr(audio, "resolve_ffmpeg", lambda: "ffmpeg")
    commands = []

    def extract(command, **_kwargs):
        commands.append(command)
        Path(command[-2]).write_bytes(b"selected-wav")

    monkeypatch.setattr(audio, "run_command", extract)
    destination = tmp_path / "audio.wav"
    destination.write_bytes(b"previous")
    if mutation == "none":
        audio.extract_audio("movie.mkv", str(destination), audio_selection=selection)
        assert destination.read_bytes() == b"selected-wav"
    else:
        with pytest.raises(AudioSelectionError, match="file changed"):
            audio.extract_audio("movie.mkv", str(destination), audio_selection=selection)
        assert destination.read_bytes() == b"previous"
    assert not (tmp_path / ".audio.partial.wav").exists()
    if mutation == "before":
        assert not commands
    else:
        assert all(command[command.index("-map") + 1] == "0:3" for command in commands)


def install_probe(monkeypatch, streams, on_exit=lambda: None):
    calls = []

    class Container:
        def __enter__(self):
            return SimpleNamespace(streams=streams)

        def __exit__(self, *_args):
            on_exit()

    def open_media(path, **kwargs):
        calls.append((path, kwargs))
        return Container()

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=open_media))
    return calls


class Streams(list):
    @property
    def audio(self):
        return self


def stream(metadata=None):
    return SimpleNamespace(
        index=2,
        metadata=metadata or {},
        disposition=1,
        codec_context=SimpleNamespace(name="aac", channels=2),
        time_base=Fraction(1, 48000),
        start_time=24000,
        duration=240000,
    )


def test_probe_preserves_unknown_tags_and_observed_timing(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"synthetic")
    calls = install_probe(monkeypatch, Streams([stream()]))
    inventory = probe_audio_tracks(source)
    assert inventory == probe_audio_tracks(source)
    selected = inventory.select().track
    assert selected.language is None and selected.title is None
    assert selected.start_seconds == 0.5
    assert selected.duration_seconds == 5
    assert selected.time_base == "1/48000"
    assert calls[0][1] == {"options": PROBE_OPTIONS, "timeout": 5.0}


def test_probe_token_changes_for_metadata_and_file_state(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"synthetic")
    streams = Streams([stream({"language": "eng", "title": "Main"})])
    install_probe(monkeypatch, streams)
    original = probe_audio_tracks(source)
    streams[0].metadata["title"] = "Commentary"
    assert probe_audio_tracks(source).file_version != original.file_version
    streams[0].metadata["title"] = "Main"
    source.write_bytes(b"replacement")
    assert probe_audio_tracks(source).file_version != original.file_version


def test_probe_rejects_inflight_mutation(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"synthetic")
    install_probe(monkeypatch, Streams([stream()]), lambda: source.write_bytes(b"changed"))
    with pytest.raises(AudioSelectionError, match="changed while probing"):
        probe_audio_tracks(source)


@pytest.mark.parametrize(
    "streams",
    [
        Streams([stream()] * (MAX_STREAMS + 1)),
        Streams([stream({"title": "x" * 513})]),
        Streams([stream(), stream()]),
    ],
)
def test_probe_rejects_oversized_or_invalid_inventory(tmp_path, monkeypatch, streams):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"synthetic")
    install_probe(monkeypatch, streams)
    with pytest.raises(AudioSelectionError):
        probe_audio_tracks(source)


def test_generated_multitrack_media_probes_and_extracts_distinct_inputs(tmp_path):
    from scanner.pipeline.audio import extract_audio
    from scanner.runtime.runtime_support import resolve_ffmpeg

    source = tmp_path / "generated.mkv"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-map",
            "0:a",
            "-map",
            "1:a",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:0",
            "title=Main",
            "-metadata:s:a:1",
            "language=eng",
            "-metadata:s:a:1",
            "title=Commentary",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "0",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    inventory = probe_audio_tracks(source)
    assert len(inventory.tracks) == 2
    assert [track.language for track in inventory.tracks] == ["eng", "eng"]
    assert [track.title for track in inventory.tracks] == ["Main", "Commentary"]
    assert inventory.preselected_stream_index is None
    outputs = []
    for observed in inventory.tracks:
        destination = tmp_path / f"track-{observed.stream_index}.wav"
        extract_audio(
            str(source), str(destination), audio_selection=inventory.select(observed.stream_index)
        )
        outputs.append(destination.read_bytes())
    assert all(output.startswith(b"RIFF") for output in outputs)
    assert outputs[0] != outputs[1]
