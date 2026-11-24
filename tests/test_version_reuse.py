"""Version-bound cue projection, transcript gaps, and verified target writes."""

from hashlib import md5, sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from scanner import api as root
from scanner.db import MediaItemRow, ScanFindingRow, ScanRow
from scanner.http import identity_verification_api as api
from scanner.modeling.media_identity import normalize_jellyfin
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.modeling.model_version_reuse import (
    ReuseAnchor,
    VersionReuseApproval,
    VersionReuseRequest,
)
from scanner.modeling.version_alignment import Alignment, TimedWord, transcript_anchors
from scanner.models import JellyfinMediaItem
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.repository.repository_audio_tracks import (
    record_analyzed_audio_selection,
    recorded_playback_offset,
)
from scanner.persistence.repository.repository_review_core import _scan_is_available
from scanner.persistence.repository.version_reuse import approved_reuse, bound_scan_file_matches
from scanner.persistence.rows.db_media_identity import MediaVersionLocatorRow, MediaVersionRow
from scanner.runtime import version_reuse_audio
from scanner.runtime.media_fingerprints import (
    SampledMediaFingerprint,
    content_digest,
    sampled_media_fingerprint,
)
from scanner.runtime.scan_version import file_generation
from scanner.security.access.capability_ids import Capability
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.review_repository_support import review_db
from tests.test_review_access_http import app as app


def _fixture_fingerprint(path: Path) -> SampledMediaFingerprint:
    data = path.read_bytes()
    return SampledMediaFingerprint(
        path, md5(data, usedforsecurity=False).hexdigest(), len(data), file_generation(str(path))
    )


def test_piecewise_alignment_maps_only_continuous_covered_cues():
    alignment = Alignment.from_anchors(
        [
            ReuseAnchor(source_seconds=0, target_seconds=10),
            ReuseAnchor(source_seconds=10, target_seconds=20),
            ReuseAnchor(source_seconds=20, target_seconds=40, segment=1),
            ReuseAnchor(source_seconds=30, target_seconds=50, segment=1),
        ]
    )
    assert alignment.project(2, 4) == (12, 14)
    assert alignment.project(23, None) == (43, None)
    assert alignment.project(10, 20) is None
    assert alignment.project(12, None) is None
    with pytest.raises(ValueError, match="increase"):
        Alignment.from_anchors(
            [
                ReuseAnchor(source_seconds=0, target_seconds=10),
                ReuseAnchor(source_seconds=10, target_seconds=5),
            ]
        )


def test_transcript_alignment_leaves_an_inserted_scene_unmapped():
    a = "mark every chord you cross during tonight's noisy live outdoor performance".split()
    b = "never forget those woven blankets before hopping aboard your small wagon".split()
    left = [
        TimedWord(text, start + i * 0.4, start + i * 0.4 + 0.3)
        for start, words in [(2, a), (20, b)]
        for i, text in enumerate(words)
    ]
    right = [
        TimedWord(text, start + i * 0.4, start + i * 0.4 + 0.3)
        for start, words in [(4, a), (35, b)]
        for i, text in enumerate(words)
    ]
    anchors = transcript_anchors(left, right)
    assert {point.segment for point in anchors} == {0, 1}
    alignment = Alignment.from_anchors(anchors)
    assert alignment.project(2.5, 3) == (4.5, 5)
    assert alignment.project(11, 12) is None
    assert alignment.project(20.6, 21) == (35.6, 36)


def test_pending_import_remains_visible_but_ineligible_for_membership():
    with review_db() as db:
        row = db.get(ScanRow, "scan")
        row.origin = "imported"
        row.checkpoint_state = '{"identity_reuse_pending":true}'
        assert not _scan_is_available(db, row, media_item_id="media", media_source_id=None)


def test_a_weak_historical_pin_cannot_verify_physical_evidence(tmp_path):
    path = tmp_path / "same-name.mkv"
    path.write_bytes(b"changed in the middle")
    with review_db() as db:
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(path)
        scan.video_hash = "legacy-sampled"
        title_id = identity.observe_title(db, scan.media_item)
        version_id = "migration-only-version"
        db.add(MediaVersionRow(id=version_id, title_id=title_id, video_hash=scan.video_hash))
        db.add(
            MediaVersionLocatorRow(
                version_id=version_id,
                media_item_id=scan.media_item_id,
                media_source_id="",
                source_path=str(path),
                resolved_path=str(path),
            )
        )
        identity.bind_scan_version(db, scan.id, version_id)
        assert identity.get_version(db, version_id).content_sha256 is None
        assert bound_scan_file_matches(scan, path) is False


def test_track_cache_can_be_aligned_after_original_file_is_deleted(tmp_path, monkeypatch):
    selection = AudioSelection.model_validate(
        {
            "file_version": "a" * 64,
            "track": {"stream_index": 1, "disposition": 0},
        }
    )
    words = "mark every chord you cross during tonight's noisy live outdoor performance".split()
    segments = [
        {
            "words": [
                {"word": word, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
                for i, word in enumerate(words)
            ],
        }
    ]
    original = tmp_path / "deleted-source.mkv"
    target = tmp_path / "replacement.mkv"
    target.write_bytes(b"target")
    probes = []

    def probe(path):
        probes.append(path)
        assert path == target
        return SimpleNamespace(revalidate=lambda _selection: None)

    monkeypatch.setattr(version_reuse_audio, "cached_segments", lambda _audio: segments)
    monkeypatch.setattr(version_reuse_audio, "probe_audio_tracks", probe)
    monkeypatch.setattr(version_reuse_audio, "audio_playback_start", lambda *_a, **_kw: 3.0)
    with review_db() as db:
        scan = db.get(ScanRow, "scan")
        scan.requested_audio_selection_json = selection.model_dump_json()
        scan.resolved_local_path = str(original)
        db.commit()
        record_analyzed_audio_selection(db, scan.id, selection, playback_offset_seconds=1.0)
        assert recorded_playback_offset(scan, selection.selection()) == 1.0
        anchors = version_reuse_audio.match_audio_transcripts(
            scan,
            target,
            selection.selection(),
            (None,),
        )
        assert probes == [target]
        assert Alignment.from_anchors(anchors).project(2, 2.2) == (4, 4.2)


@pytest.mark.parametrize("deferred", (False, True))
@pytest.mark.parametrize("source_name", ("chuck profanity 1", None))
def test_approval_copies_only_mapped_cues_and_reuses_the_same_result(
    tmp_path, monkeypatch, deferred, source_name
):
    old_path = tmp_path / "original.mkv"
    new_path = tmp_path / "replacement.mkv"
    old_path.write_bytes(b"original")
    new_path.write_bytes(b"replacement")
    with review_db() as db:
        original = db.get(ScanRow, "scan")
        original.name = source_name
        original.scan_video = True
        original.media_item.path = str(old_path)
        original.resolved_local_path = str(old_path)
        input_identity = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Tmdb": "42"}})
        identity.observe_title(db, original.media_item, input_identity)
        old_version = identity.observe_version(
            db,
            media=original.media_item,
            media_source_id=None,
            source_path=str(old_path),
            resolved_path=str(old_path),
            content_sha256=content_digest(old_path),
            scan_id=original.id,
        )
        movie = MediaItemRow(
            id="other",
            server_id="server",
            jellyfin_id="new",
            item_type="Movie",
            name="Movie",
            path=str(new_path),
        )
        db.add(movie)
        db.flush()
        identity.observe_title(db, movie, input_identity)
        new_version = identity.observe_sampled_version(
            db,
            media=movie,
            media_source_id=None,
            source_path=str(new_path),
            fingerprint=_fixture_fingerprint(new_path),
        )
        for start in (2, 20):
            db.add(
                ScanFindingRow(
                    scan_id="scan",
                    source="visual",
                    start_seconds=start,
                    end_seconds=start + 1,
                    edit_action="skip",
                    label=f"Evidence at {start}",
                )
            )
        db.commit()

        prepared = SimpleNamespace(
            media=SimpleNamespace(resolved_path=str(new_path)),
            source=SimpleNamespace(
                server_id="server",
                item_id="new",
                media_source_id=None,
                path=str(new_path),
                duration_seconds=100,
                name="The Movie",
                media_source_name="Remux",
            ),
        )
        actor = SimpleNamespace(user_id=1, permission_revision=5)
        caller = SimpleNamespace(
            db=db,
            access=SimpleNamespace(principal=actor),
            require_scan=lambda *_args: None,
            media_decision=lambda _media: None,
            create=lambda _media, persist, **_options: persist(db),
        )
        pinned_target = identity.get_version(db, new_version)
        route_version = api.CURRENT_VERSION if deferred else new_version
        preview_target = api.UnpinnedTarget(pinned_target.title_id) if deferred else pinned_target
        monkeypatch.setattr(api, "_target", lambda *_args: (prepared, preview_target))
        reads = []

        def fingerprint(path, *, on_progress=None):
            reads.append(path)
            if deferred:
                [activity] = api.fingerprint_queue(caller)
                assert (activity.item_name, activity.version_name) == ("The Movie", "Remux")
                assert not activity.reading
                assert not api.active_fingerprints((1, 6))
                assert not api.active_fingerprints((2, 5))
                on_progress(5, 10)
                [progress] = api.fingerprint_queue(caller)
                assert (progress.bytes_read, progress.bytes_total, progress.reading) == (
                    5,
                    10,
                    True,
                )
            return sampled_media_fingerprint(path)

        monkeypatch.setattr(api, "sampled_media_fingerprint", fingerprint)
        monkeypatch.setattr(
            api, "observe_sampled_target_version", lambda *_args, **_kw: new_version
        )
        monkeypatch.setattr(root, "get_video_duration", lambda _path: 100)
        monkeypatch.setattr(api.ScanCaller, "present", lambda _self, scan: scan)
        request = VersionReuseRequest(
            source_scan_id="scan",
            target={"source": "jellyfin", "item_id": "new"},
            channels=["video"],
            anchors=[
                ReuseAnchor(source_seconds=1, target_seconds=4),
                ReuseAnchor(source_seconds=10, target_seconds=13),
            ],
        )
        result = api.preview_version_reuse(route_version, request, caller)
        assert reads == ([] if deferred else [new_path])
        assert result.target_generation
        assert not result.exact
        assert [cue.target_start for cue in result.cues] == [5, None]
        assert db.query(ScanRow).count() == 1
        approval = VersionReuseApproval(**request.model_dump(), token=result.token, confirmed=True)
        approved = api.approve_version_reuse(route_version, approval, caller)
        assert reads[-1] == new_path
        assert api.fingerprint_queue(caller) == []
        assert not approved.reused
        assert approved.scan.stable_version_id == new_version
        assert identity.scan_version_id(db, "scan") == old_version
        derived = db.get(ScanRow, approved.scan.id)
        assert approved.scan.name == derived.name == source_name
        assert approved.scan.verification_coverage.source_counts == {"audio": 1, "video": 2}
        assert approved.scan.verification_coverage.covered_finding_ids == {
            "audio": [],
            "video": [result.cues[0].source_finding_id],
        }
        assert derived.import_source_name == "Verified reuse"
        assert approved_reuse(derived)["source_scan_id"] == "scan"
        assert [
            (row.start_seconds, row.end_seconds)
            for row in db.query(ScanFindingRow).filter_by(
                scan_id=derived.id,
            )
        ] == [(5, 6)]
        assert api.approve_version_reuse(route_version, approval, caller).scan.id == derived.id
        assert bound_scan_file_matches(derived, Path(new_path))
        new_path.write_bytes(b"modified")
        assert not bound_scan_file_matches(derived, Path(new_path))
        with pytest.raises(HTTPException) as error:
            api.approve_version_reuse(route_version, approval, caller)
        assert error.value.status_code == 409


def _register_http_reuse_target(app, monkeypatch, *, legacy_target=False):
    """Register two files for one stable title and return its reuse request fields."""
    app.client.app.include_router(api.router)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 100)
    original_id, _ = app.seed_scan(USER, "Original cut", status="complete")
    target_scan_id, _ = app.seed_scan(USER, "Replacement cut", status="complete")
    with app.accounts.factory() as db:
        old = db.get(ScanRow, original_id)
        new = db.get(ScanRow, target_scan_id)
        input_identity = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Tmdb": "127"}})
        for row in (old, new):
            media = row.media_item
            path = Path(row.resolved_local_path)
            path.write_bytes(media.name.encode())
            media.path = str(path)
            app.items[media.jellyfin_id] = JellyfinMediaItem(
                id=media.jellyfin_id,
                name=media.name,
                item_type="Movie",
                library_id=media.library_id,
                path=media.path,
                duration_seconds=100,
                media_identity=input_identity,
            )
            identity.observe_title(db, media, input_identity)
            row.duration_seconds = 100
            if legacy_target and row is new:
                version_id = identity.observe_version(
                    db,
                    media=media,
                    media_source_id=None,
                    source_path=str(path),
                    resolved_path=str(path),
                    content_sha256=sha256(path.read_bytes()).hexdigest(),
                    scan_id=row.id,
                )
            else:
                version_id = identity.observe_sampled_version(
                    db,
                    media=media,
                    media_source_id=None,
                    source_path=str(path),
                    fingerprint=_fixture_fingerprint(path),
                    scan_id=row.id,
                )
        db.add(
            ScanFindingRow(
                scan_id=original_id,
                source="visual",
                start_seconds=2,
                end_seconds=4,
                edit_action="skip",
            )
        )
        target_item = new.media_item.jellyfin_id
        db.commit()
    return original_id, target_item, version_id


def test_reuse_http_requires_authorized_retiming(app, monkeypatch):
    original_id, target_item, version_id = _register_http_reuse_target(app, monkeypatch)
    body = {
        "source_scan_id": original_id,
        "target": {"source": "jellyfin", "item_id": target_item},
        "channels": ["video"],
        "method": "original",
    }
    path = f"/api/media-versions/{version_id}/reuse"
    app.accounts.set_override(USER, Capability.REVIEWS_TIMING_EDIT_OWN, OverrideEffect.DENY)
    preview = app.send("POST", f"{path}/preview", app.accounts.unsafe_cookie(USER), body)
    assert preview.status_code == 200, preview.text
    denied = app.send(
        "POST",
        f"{path}/approve",
        app.accounts.unsafe_cookie(USER),
        {
            **body,
            "token": preview.json()["token"],
            "confirmed": True,
        },
    )
    assert denied.status_code == 403, denied.text


def test_transcript_status_is_scoped_to_an_authorized_target_track(app, monkeypatch):
    original_id, target_item, _ = _register_http_reuse_target(app, monkeypatch)
    read_tracks = []

    def selected_track(index):
        return AudioSelection.model_validate(
            {"file_version": "b" * 64, "track": {"stream_index": index, "disposition": 0}}
        ).selection()

    def cached_segments(audio):
        index = audio.selection.track.stream_index
        read_tracks.append(index)
        if index == 4:
            return [{"words": [{"word": "dialogue", "start_seconds": 2, "end_seconds": 3}]}]
        return [{"text": "No word-level timestamps on this track"}]

    monkeypatch.setattr(
        version_reuse_audio,
        "probe_audio_tracks",
        lambda _path: SimpleNamespace(select=selected_track),
    )
    monkeypatch.setattr(version_reuse_audio, "cached_segments", cached_segments)
    path = "/api/media-versions/current/reuse/transcript-status"
    headers = app.accounts.unsafe_cookie(USER)
    body = {
        "source_scan_id": original_id,
        "target": {"source": "jellyfin", "item_id": target_item},
        "channels": ["audio"],
        "method": "transcript",
    }
    for track, ready in [(4, True), (1, False)]:
        result = app.send("POST", path, headers, {**body, "audio_track_index": track})
        assert result.status_code == 200, result.text
        assert result.json() == {"ready": ready}
    denied = app.send(
        "POST", path, headers, {**body, "source_scan_id": "absent", "audio_track_index": 4}
    )
    assert denied.status_code == 404, denied.text
    app.accounts.set_override(USER, Capability.REVIEWS_IMPORT_UPLOAD, OverrideEffect.DENY)
    forbidden = app.send(
        "POST", path, app.accounts.unsafe_cookie(USER), {**body, "audio_track_index": 4}
    )
    assert forbidden.status_code == 403, forbidden.text
    assert read_tracks == [4, 1]


def test_verification_reuses_moved_transcript_only_after_matching_sample(app, monkeypatch):
    original_id, target_item, _ = _register_http_reuse_target(app, monkeypatch)
    requested = AudioSelection.model_validate(
        {"file_version": "1" * 64, "track": {"stream_index": 1, "disposition": 1}}
    )
    current = requested.model_copy(update={"file_version": "2" * 64}).selection()
    with app.accounts.factory() as db:
        row = db.query(ScanRow).join(MediaItemRow).filter_by(jellyfin_id=target_item).one()
        row.scan_audio = True
        row.requested_audio_selection_json = requested.model_dump_json()
        record_analyzed_audio_selection(db, row.id, requested)
        path = Path(row.resolved_local_path)

    monkeypatch.setattr(
        version_reuse_audio,
        "probe_audio_tracks",
        lambda _: SimpleNamespace(select=lambda _: current, revalidate=lambda _: current.track),
    )
    monkeypatch.setattr(version_reuse_audio, "audio_playback_start", lambda *_, **__: 0)
    monkeypatch.setattr(
        version_reuse_audio,
        "cached_segments",
        lambda audio: (
            [{"words": [{"word": "dialogue", "start_seconds": 2, "end_seconds": 3}]}]
            if audio.selection == requested.selection()
            else None
        ),
    )
    reads = []

    def read_sample(selected, **kwargs):
        reads.append(selected)
        return sampled_media_fingerprint(selected, **kwargs)

    monkeypatch.setattr(api, "sampled_media_fingerprint", read_sample)
    route = "/api/media-versions/current/reuse"
    body = {
        "source_scan_id": original_id,
        "target": {"source": "jellyfin", "item_id": target_item},
        "channels": ["audio"],
        "method": "transcript",
        "audio_track_index": 1,
    }
    headers = app.accounts.unsafe_cookie(USER)
    preflight = app.send("POST", f"{route}/transcript-status", headers, body)
    assert preflight.json() == {"ready": False, "verify_existing": True}, preflight.text
    assert reads == []

    preview = app.send("POST", f"{route}/preview", headers, body)
    assert preview.status_code == 200, preview.text
    assert "Couldn’t align automatically" in preview.json()["issues"][0]
    assert reads == [path]
    ready = app.send("POST", f"{route}/transcript-status", headers, body)
    assert ready.json() == {"ready": True}, ready.text

    path.write_bytes(b"replacement cut")
    sampled_media_fingerprint(path)
    unavailable = app.send("POST", f"{route}/transcript-status", headers, body)
    assert unavailable.json() == {"ready": False}, unavailable.text
