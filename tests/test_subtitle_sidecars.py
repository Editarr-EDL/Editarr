"""Shared subtitle publication never adopts another writer's same-byte sidecar."""

import pytest

from scanner.modeling.model_subtitles import SubtitleSource
from scanner.runtime import subtitle_sidecars as sidecars


def test_external_same_byte_winner_is_not_claimed(tmp_path, monkeypatch):
    video = tmp_path / "Movie.mkv"
    track = SubtitleSource(
        id="a" * 32, label="English", language="en", format="srt", kind="external"
    )
    slot = sidecars.media_slot(video, "version")
    destination = sidecars.publication_path(video, track)
    payload = b"timed subtitle bytes"
    actual_link = sidecars.os.link

    def won_by_external_writer(source, target):
        if target == destination:
            destination.write_bytes(payload)
        actual_link(source, target)

    monkeypatch.setattr(sidecars.os, "link", won_by_external_writer)
    with pytest.raises(sidecars.SubtitleConflict, match="appeared"):
        sidecars.publish_sidecar(video, slot, track, payload)

    assert destination.read_bytes() == payload
    assert not sidecars.discover_publications(video, slot)
    with pytest.raises(sidecars.SubtitleConflict, match="different"):
        sidecars.publish_sidecar(video, slot, track, b"second attempt")
    assert destination.read_bytes() == payload


def test_shared_lock_prevents_replacing_a_writers_ownership(tmp_path):
    video = tmp_path / "Movie.mkv"
    track = SubtitleSource(
        id="a" * 32, label="English", language="en", format="srt", kind="embedded"
    )
    slot = sidecars.media_slot(video, "version")
    destination = sidecars.publication_path(video, track)
    with sidecars._destination_lock(sidecars._record_path(destination)):
        with pytest.raises(sidecars.SubtitleConflict, match="already"):
            sidecars.publish_sidecar(video, slot, track, b"left by the losing writer")
    assert not destination.exists()
    assert not sidecars.publish_sidecar(video, slot, track, b"owned")
    assert sidecars.discover_publications(video, slot)[0].filename == destination.name
