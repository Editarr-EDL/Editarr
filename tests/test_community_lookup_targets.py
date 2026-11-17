"""Generated-only file/track capture and read-only provider target resolution."""

import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scanner.integrations.community import lookup_targets as targets
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_matching import classify
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.modeling.model_community_lookup import LookupRequest, LookupTarget
from scanner.pipeline.audio_tracks import probe_audio_tracks
from tests.test_stream_fingerprints import generated_streams as generated_streams


@pytest.fixture
def media():
    return MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "123", "provider.movie"),))


def request(selection=None, **changes):
    return LookupRequest(
        target=LookupTarget(source="jellyfin", item_id="movie", media_source_id="version-1"),
        audio_selection=selection,
        **changes,
    )


def test_generated_selected_tracks_are_private_and_video_is_independent(
    generated_streams, monkeypatch, media
):
    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))
    inventory = probe_audio_tracks(path)
    first = targets.capture_target(
        None, request(AudioSelection.from_selection(inventory.select(1)))
    )
    second = targets.capture_target(
        None, request(AudioSelection.from_selection(inventory.select(2)))
    )
    assert first.key != second.key
    assert first.evidence.video_stream == second.evidence.video_stream
    assert first.evidence.audio_stream.digest != second.evidence.audio_stream.digest
    assert first.evidence.audio_stream.provenance == "selected"
    assert first.evidence.media.release.duration.source == "file_probe"
    assert first.evidence.media.release.size_bytes == path.stat().st_size
    public = json.dumps(asdict(first.evidence))
    for private in (str(path), "file_version", "stream_index", "tracks", "time_base"):
        assert private not in public
    matching = classify(
        first.evidence,
        replace(
            second.evidence,
            audio_stream=replace(second.evidence.audio_stream, provenance="analyzed"),
        ),
    )
    assert matching.video.strong_eligible
    assert not matching.audio.strong_eligible


def test_unknown_audio_and_unsupported_streams_do_not_fabricate_evidence(
    generated_streams, monkeypatch, media
):
    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))
    unknown = targets.capture_target(None, request())
    assert unknown.evidence.audio_stream is None
    assert unknown.evidence.media.release.audio is None
    assert "audio_selection_required" in unknown.warnings

    def unsupported(*_args, **_kwargs):
        raise targets.StreamUnsupported("audio_pcm_unavailable")

    monkeypatch.setattr(targets, "fingerprint_pcm_audio", unsupported)
    inventory = probe_audio_tracks(path)
    value = targets.capture_target(
        None, request(AudioSelection.from_selection(inventory.select(1)))
    )
    assert value.evidence.video_stream is not None and value.evidence.audio_stream is None
    assert "audio_fingerprint_unsupported" in value.warnings


def test_stale_track_and_changed_file_reject_results(generated_streams, monkeypatch, media):
    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))
    inventory = probe_audio_tracks(path)
    stale = AudioSelection.from_selection(inventory.select(1)).model_copy(
        update={"file_version": "f" * 64}
    )
    with pytest.raises(CommunityFailure, match="audio_selection_stale"):
        targets.capture_target(None, request(stale))
    captured = targets.capture_target(None, request())
    path.write_bytes(b"changed")
    with pytest.raises(CommunityFailure, match="file_changed"):
        captured.check()


def test_cursor_is_bound_to_file_track_and_policy_and_cancellation_is_cooperative(
    generated_streams, monkeypatch, media
):
    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))
    original = targets.capture_target(None, request())
    assert (
        targets.capture_target(None, request(cursor="opaque", target_key=original.key)).key
        == original.key
    )
    with pytest.raises(CommunityFailure, match="target_changed"):
        targets.capture_target(None, request(cursor="opaque", target_key="f" * 64))
    with pytest.raises(CommunityFailure, match="target_changed"):
        targets.capture_target(
            None, request(cursor="opaque", target_key=original.key, refresh=True)
        )
    with pytest.raises(CommunityFailure, match="lookup_cancelled"):
        targets.capture_target(None, request(), should_cancel=lambda: True)


def test_provider_selected_target_is_resolved_without_browser_paths(tmp_path, monkeypatch, media):
    path = tmp_path / "selected.mp4"
    path.write_bytes(b"synthetic")
    selected = SimpleNamespace(id="version-1", path=str(path), media_identity=media)
    item = SimpleNamespace(
        versions=[selected], media_identity=media, path=str(path), media_source_id="version-1"
    )

    class Provider:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, item_id):
            assert item_id == "item"
            return item

    monkeypatch.setattr(targets, "JellyfinClient", Provider)
    monkeypatch.setattr(targets, "get_jellyfin_credentials", lambda _db: ("provider", "private"))
    monkeypatch.setattr(
        targets, "resolve_local_path", lambda *_args: SimpleNamespace(local_path=str(path))
    )
    target = LookupTarget(source="jellyfin", item_id="item", media_source_id="version-1")
    assert targets.resolve_target(None, target) == (path, media)
    with pytest.raises(CommunityFailure, match="target_changed"):
        targets.resolve_target(None, target.model_copy(update={"media_source_id": "missing"}))
    for claimed in (
        {"source": "jellyfin", "item_id": "item", "video_path": "/browser/claimed.mp4"},
        {"source": "manual", "item_id": "item"},
    ):
        with pytest.raises(ValidationError):
            LookupTarget.model_validate(claimed)


def test_jellyfin_ambiguous_version_is_not_silently_selected(tmp_path, monkeypatch, media):
    path = tmp_path / "file.mp4"
    path.write_bytes(b"synthetic")

    class Provider:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, _item):
            return SimpleNamespace(
                path=str(path),
                media_identity=media,
                versions=[
                    SimpleNamespace(id=identifier, path=str(path), media_identity=media)
                    for identifier in ("a", "b")
                ],
            )

    monkeypatch.setattr(targets, "JellyfinClient", Provider)
    monkeypatch.setattr(targets, "get_jellyfin_credentials", lambda _db: ("provider", "private"))
    monkeypatch.setattr(
        targets, "resolve_local_path", lambda _db, value: SimpleNamespace(local_path=value)
    )
    with pytest.raises(CommunityFailure, match="version_required"):
        targets.resolve_target(None, LookupTarget(source="jellyfin", item_id="item"))


@pytest.mark.parametrize(
    "code,expected", [("cancelled", "lookup_cancelled"), ("file_changed", "file_changed")]
)
def test_sampling_cancellation_and_mutation_keep_machine_codes(
    generated_streams, monkeypatch, media, code, expected
):
    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))

    def fail(*_args, **_kwargs):
        raise targets.StreamUnsupported(code)

    monkeypatch.setattr(targets, "fingerprint_video", fail)
    with pytest.raises(CommunityFailure, match=expected):
        targets.capture_target(None, request())


@pytest.mark.parametrize(
    "title,expected",
    [("English / Commentary", "English / Commentary"), ("/private/movie.mkv", None)],
)
def test_preserves_selected_language_description_but_not_path_text(
    generated_streams, monkeypatch, media, title, expected
):
    path, generate = generated_streams
    tagged = generate(
        "tagged.mp4",
        [
            "-i",
            str(path),
            "-map",
            "0",
            "-c",
            "copy",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:0",
            f"title={title}",
        ],
    )
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (tagged, media))
    original_probe = probe_audio_tracks(tagged)
    track = replace(original_probe.select(1).track, title=title)
    selected = replace(original_probe.select(1), track=track)
    inventory = replace(
        original_probe,
        tracks=tuple(track if item.stream_index == 1 else item for item in original_probe.tracks),
    )
    monkeypatch.setattr(targets, "probe_audio_tracks", lambda _path: inventory)
    from scanner.integrations.community import pcm_fingerprints

    monkeypatch.setattr(pcm_fingerprints, "probe_audio_tracks", lambda _path: inventory)
    captured = targets.capture_target(None, request(AudioSelection.from_selection(selected)))
    assert captured.evidence.media.release.audio.language == "eng"
    assert captured.evidence.audio_stream.description == expected


@pytest.mark.parametrize(
    "error", [ValueError("unknown duration"), targets.StreamUnsupported("read_budget")]
)
def test_duration_is_unknown_when_bounded_metadata_is_unavailable(
    generated_streams, monkeypatch, media, error
):
    from scanner.pipeline import frames

    path, _generate = generated_streams
    monkeypatch.setattr(targets, "resolve_target", lambda *_args: (path, media))
    monkeypatch.setattr(
        frames, "get_video_duration", lambda *_args: pytest.fail("must not count the whole file")
    )

    def unavailable(*_args):
        raise error

    monkeypatch.setattr(targets, "probe_video_duration", unavailable)
    captured = targets.capture_target(None, request())
    assert captured.evidence.media.release.duration is None
    assert "measured_duration_unavailable" in captured.warnings
