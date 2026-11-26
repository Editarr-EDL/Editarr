"""Shared subtitle publication never adopts another writer's same-byte sidecar."""

import json
from dataclasses import replace

import pytest

from scanner.modeling.model_subtitles import SubtitleSource
from scanner.runtime import subtitle_sidecars as sidecars
from scanner.runtime.subtitle_publication_names import (
    NamedSubtitlePublication,
    SubtitleNameError,
    named_publication_path,
)


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


def test_legacy_owner_record_survives_a_remount(tmp_path):
    video = tmp_path / "Movie.mkv"
    track = SubtitleSource(
        id="a" * 32, label="English", language="en", format="srt", kind="external"
    )
    slot = sidecars.media_slot(video, "version")
    destination = sidecars.publication_path(video, track)
    assert not sidecars.publish_sidecar(video, slot, track, b"owned")
    record_path = sidecars._record_path(destination)
    record = json.loads(record_path.read_text())
    record["inputs"][0]["inode"].insert(0, 727)
    record_path.write_text(json.dumps(record))
    assert sidecars.discover_publications(video, slot)[0].filename == destination.name
    assert sidecars.publish_sidecar(video, slot, track, b"replacement")
    assert destination.read_bytes() == b"replacement"


def _named_output(tmp_path):
    video = tmp_path / "Movie.mkv"
    track = SubtitleSource(
        id="a" * 32, label="English", language="en", format="srt", kind="embedded"
    )
    publication = NamedSubtitlePublication("e" * 64, "review-a", "cut-a", "Sam - Dialogue")
    return video, sidecars.media_slot(video, "jellyfin-version"), track, publication


def _write_named(media, slot, source, publication, content):
    path = named_publication_path(media, source, publication, frozenset({"en"}))
    return sidecars.publish_named_sidecar(media, slot, source, content, publication, path)


@pytest.mark.parametrize("different_key", ["publisher", "review", "version", "source"])
def test_identical_visible_name_cannot_replace_another_publication(tmp_path, different_key):
    media, slot, source, publication = _named_output(tmp_path)
    written, replaced = _write_named(media, slot, source, publication, b"first")
    if different_key == "source":
        source = source.model_copy(update={"id": "b" * 32})
    else:
        publication = replace(publication, **{different_key: "d" * 64})
    with pytest.raises(sidecars.SubtitleConflict, match="distinct saved review title"):
        _write_named(media, slot, source, publication, b"second")
    assert replaced is False
    assert (media.parent / written).read_bytes() == b"first"


def test_owned_name_survives_incomplete_rename_and_retires_on_retry(tmp_path, monkeypatch):
    media, slot, source, publication = _named_output(tmp_path)
    old, _ = _write_named(media, slot, source, publication, b"old revision")
    other = replace(publication, review="other", title="Sam - Family review")
    other_name, _ = _write_named(media, slot, source, other, b"other review")
    renamed = replace(publication, title="Sam - Revised dialogue")

    def unavailable(_path, _target):
        raise OSError("Shares can temporarily become unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(sidecars.os, "link", unavailable)
        with pytest.raises(OSError, match="temporarily"):
            _write_named(media, slot, source, renamed, b"new revision")

    assert (media.parent / old).read_bytes() == b"old revision"
    new, replaced = _write_named(media, slot, source, renamed, b"new revision")
    assert replaced is True
    assert set(p.filename for p in sidecars.discover_publications(media, slot)) == {
        new,
        other_name,
    }
    assert (media.parent / new).read_bytes() == b"new revision"
    assert (media.parent / other_name).read_bytes() == b"other review"
    assert not (media.parent / old).exists()


def test_case_only_rename_keeps_previous_file_and_explains_conflict(tmp_path):
    media, slot, source, publication = _named_output(tmp_path)
    original, _ = _write_named(media, slot, source, publication, b"own version")
    publication = replace(publication, title=publication.title.upper())
    with pytest.raises(sidecars.SubtitleConflict, match="case-insensitive match"):
        _write_named(media, slot, source, publication, b"new version")
    assert (media.parent / original).read_bytes() == b"own version"


@pytest.mark.parametrize("language", [None, "hi"])
def test_only_real_language_tokens_conflict_with_dots(tmp_path, language):
    media, _slot, source, publication = _named_output(tmp_path)
    source = source.model_copy(update={"language": language})
    publication = replace(publication, title="Dr.Sam - Version 3.1")
    tokens = frozenset({"en", "english", "hi", "hin"})
    assert publication.title in named_publication_path(media, source, publication, tokens).name
    publication = replace(publication, title="Sam.English - Version.en")
    with pytest.raises(SubtitleNameError, match="period-separated parts as a language"):
        named_publication_path(media, source, publication, tokens)
