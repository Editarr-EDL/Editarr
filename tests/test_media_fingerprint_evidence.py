"""Reuse observed file generations across approvals and restarted processes."""

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from scanner.db import MediaItemRow, ScanRow
from scanner.integrations.community.fingerprints import FingerprintCache
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.repository.repository_audio_tracks import record_analyzed_audio_selection
from scanner.pipeline import audio_tracks
from scanner.runtime import media_fingerprints
from scanner.runtime.file_generation import file_generation
from tests.multi_user_access_support import USER
from tests.test_version_reuse import _register_http_reuse_target
from tests.test_version_reuse import app as app


@pytest.fixture
def fresh_fingerprint_process(tmp_path, monkeypatch):
    monkeypatch.setattr(media_fingerprints.settings, "data_dir", tmp_path / "editarr-data")

    def restart():
        monkeypatch.setattr(
            media_fingerprints, "_fingerprints", FingerprintCache(track_full_reads=True)
        )

    restart()
    return restart


def test_full_read_remains_reusable_after_process_restart(
    tmp_path, monkeypatch, fresh_fingerprint_process
):
    path = tmp_path / "remux.mkv"
    path.write_bytes(b"exact bytes")
    original = sha256(b"exact bytes").hexdigest()
    assert media_fingerprints.content_digest(path) == original

    fresh_fingerprint_process()
    with monkeypatch.context() as patched:
        patched.setattr(
            media_fingerprints._fingerprints,
            "_read_digest",
            lambda *_: pytest.fail("Unchanged media was read again"),
        )
        assert media_fingerprints.cached_content_digest(path) == original
        assert media_fingerprints.content_digest(path) == original

    path.write_bytes(b"other bytes")
    assert media_fingerprints.cached_content_digest(path) is None
    assert media_fingerprints.content_digest(path) == sha256(b"other bytes").hexdigest()


def test_media_generations_ignore_mount_device_but_distinguish_paths(tmp_path):
    path = tmp_path / "remux.mkv"
    path.write_bytes(b"unchanged")
    metadata = path.stat()
    fields = {
        name: getattr(metadata, name)
        for name in ("st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
    }
    before = SimpleNamespace(st_dev=41, **fields)
    after = SimpleNamespace(st_dev=94, **fields)
    assert file_generation(before, path) == file_generation(after, path)
    assert file_generation(before, path) != file_generation(after, path.with_name("other.mkv"))

    class RemountedPath:
        def __init__(self, state):
            self.state = state

        def stat(self):
            return self.state

    assert audio_tracks._file_state(RemountedPath(before)) == audio_tracks._file_state(
        RemountedPath(after)
    )


def test_legacy_exact_fingerprint_survives_mount_device_change(tmp_path, fresh_fingerprint_process):
    path = (tmp_path / "remux.mkv").resolve()
    path.write_bytes(b"exact bytes")
    generation = file_generation(path.stat(), path)
    legacy_stamp = (5127, *generation[1:])
    digest = sha256(b"exact bytes").hexdigest()
    record = media_fingerprints._witness_path(path)
    record.parent.mkdir(mode=0o700, parents=True)
    record.write_bytes(("v1 " + " ".join(map(str, legacy_stamp)) + "\n" + digest).encode())
    record.chmod(0o600)

    assert media_fingerprints.cached_content_digest(path) == digest
    path.write_bytes(b"other bytes")
    assert media_fingerprints.cached_content_digest(path) is None


def test_verified_legacy_probe_survives_restart_and_temporarily_unavailable_inventory(
    app,
    monkeypatch,
    fresh_fingerprint_process,
):
    source_id, target_id, target_version_id = _register_http_reuse_target(
        app, monkeypatch, legacy_target=True
    )
    with app.accounts.factory() as db:
        target = db.scalar(
            select(ScanRow).join(MediaItemRow).where(MediaItemRow.jellyfin_id == target_id)
        )
        path = Path(target.resolved_local_path)
        selection = AudioSelection.model_validate(
            {
                "file_version": "2" * 64,
                "track": {"stream_index": 1, "disposition": 1},
            }
        )
        target.scan_audio = True
        target.requested_audio_selection_json = selection.model_dump_json()
        record_analyzed_audio_selection(db, target.id, selection, playback_offset_seconds=0)
        db.commit()
    probes = []

    def transient_probe(path):
        probes.append(path)
        if len(probes) > 1:
            raise OSError("Could not read the audio inventory")
        return SimpleNamespace(file_version=selection.file_version)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("A verified generation triggered a full-file read")

    monkeypatch.setattr(audio_tracks, "probe_audio_tracks", transient_probe)
    monkeypatch.setattr(media_fingerprints._fingerprints, "_read_digest", unexpected_read)
    request = {
        "source_scan_id": source_id,
        "target": {"source": "jellyfin", "item_id": target_id},
        "channels": ["video"],
        "method": "original",
    }
    route = "/api/media-versions/current/reuse"
    headers = app.accounts.unsafe_cookie(USER)
    preview = app.send("POST", f"{route}/preview", headers, request)
    assert preview.status_code == 200, preview.text
    approval = {**request, "token": preview.json()["token"], "confirmed": True}
    assert probes == [path]

    fresh_fingerprint_process()
    monkeypatch.setattr(media_fingerprints._fingerprints, "_read_digest", unexpected_read)
    confirmed = app.send("POST", f"{route}/approve", headers, approval)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["scan"]["finding_count"] == 1
    assert confirmed.json()["scan"]["stable_version_id"] == target_version_id
    assert probes == [path]

    path.write_bytes(b"replacement generation")
    stale = app.send("POST", f"{route}/approve", headers, approval)
    assert stale.status_code == 409, stale.text


@pytest.mark.parametrize("legacy_witness", [False, True])
def test_sha_only_target_keeps_id_only_with_unchanged_verified_witness(
    app, monkeypatch, legacy_witness
):
    source, item, previous_id = _register_http_reuse_target(app, monkeypatch, legacy_target=True)
    if legacy_witness:
        media_fingerprints.content_digest(app.items[item].path)
    def unexpected_read(*_args, **_kwargs):
        pytest.fail("Historical identity triggered a full-file hash")

    monkeypatch.setattr(media_fingerprints._fingerprints, "_read_digest", unexpected_read)
    request = {
        "source_scan_id": source,
        "target": {"source": "jellyfin", "item_id": item},
        "channels": ["video"],
        "method": "original",
    }
    headers = app.accounts.unsafe_cookie(USER)
    old_endpoint = f"/api/media-versions/{previous_id}/reuse"
    old_preview = app.send("POST", f"{old_endpoint}/preview", headers, request)
    assert old_preview.status_code == (200 if legacy_witness else 409), old_preview.text
    endpoint = old_endpoint if legacy_witness else "/api/media-versions/current/reuse"
    preview = app.send("POST", f"{endpoint}/preview", headers, request)
    assert preview.status_code == 200, preview.text
    approval = app.send(
        "POST",
        f"{endpoint}/approve",
        headers,
        {
            **request,
            "token": preview.json()["token"],
            "confirmed": True,
        },
    )
    assert approval.status_code == 200, approval.text
    resolved_id = approval.json()["scan"]["stable_version_id"]
    assert (resolved_id == previous_id) is legacy_witness
    with app.accounts.factory() as db:
        assert identity.get_version(db, previous_id).content_sha256
        assert identity.get_version(db, resolved_id).sampled_md5
