"""Restore preflight tests use disposable source and target installations only."""

import hashlib
import json
import shutil
import sqlite3

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text

from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import identity_home, only_identity_ref


def _fixture(tmp_path, *, encrypted=False):
    """A source full of work and identity, and an empty target installation."""
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    source_db = source / "scanner.db"
    target_db = target / "scanner.db"
    migrate_fresh_database(source_db, source)
    migrate_fresh_database(target_db, target)
    with sqlite3.connect(source_db) as database:
        database.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (?, ?, ?)",
            (1, "private-canary", "2026-09-27"),
        )
        from scanner.modeling.media_identity import (
            MediaIdentity,
            ProviderIdentifier,
            normalize_manual,
        )

        identity_adapter = TypeAdapter(MediaIdentity)

        identity = normalize_manual(
            "movie", (ProviderIdentifier("tmdb", "movie", "501", "user_confirmed"),), confirmed=True
        )
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at, confirmed_identity_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "media-fixture",
                "http://jellyfin.test",
                "a" * 32,
                "Video",
                "Private.Movie",
                "2026-10-02",
                "2026-10-02",
                identity_adapter.dump_json(identity).decode(),
            ),
        )
    source_identity = load_or_create_installation_identity(identity_home(source))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from scanner.db import (
        AudioTrackSelectionRow,
        CommunityOperationRow,
        CommunityPublicationRow,
        ScanRow,
    )
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackSelection

    selected = AudioSelection.from_selection(
        AudioTrackSelection(
            "a" * 64, AudioTrack(1, "eng", "Main", "aac", 2, 1, 0.0, 10.0, "1/48000")
        )
    ).model_dump_json()
    engine = create_engine(f"sqlite:///{source_db}")
    with Session(engine) as database:
        database.add(
            ScanRow(
                id="audio-fixture",
                media_item_id="media-fixture",
                scan_number=1,
                source_path="/private/movie.mkv",
                resolved_local_path="/private/movie.mkv",
                requested_audio_selection_json=selected,
                analyzed_audio_selection_json=selected,
            )
        )
        database.add(
            AudioTrackSelectionRow(file_path="/private/movie.mkv", selection_json=selected)
        )
        database.commit()
        from scanner.persistence.repository.repository_community import set_connection

        set_connection(
            database,
            credential_ref=only_identity_ref(database),
            service_url=SERVICE,
            contributor_id="reader",
            installation_id=source_identity.installation_id,
            key_id=source_identity.key_id,
        )
        database.add(
            ScanRow(
                id="imported-fixture",
                media_item_id="media-fixture",
                scan_number=2,
                source_path="/private/movie.mkv",
                resolved_local_path="/private/movie.mkv",
                origin="imported",
            )
        )
        database.commit()
        _add_retired_revision_rows(database)
        database.add(
            CommunityOperationRow(
                operation_id="pending-publication",
                service_url=SERVICE,
                kind="publish",
                state="submitted",
                contributor_id="reader",
                installation_id=source_identity.installation_id,
                contribution_id="root-publication",
                revision_id="original-submission",
                digest="a" * 64,
                scan_id="audio-fixture",
                consent_version="test-consent",
                updated_at="2026-10-03",
            )
        )
        database.add(
            CommunityOperationRow(
                operation_id="vote-backup-up",
                service_url=SERVICE,
                kind="filter_vote",
                state="submitted",
                contributor_id="reader",
                installation_id=source_identity.installation_id,
                community_version_id="v_backup",
                filter_id="f_backup",
                receipt_id="b" * 64,
                vote="up",
                receipt='{"version":1,"vote":"up","receiptId":"' + "b" * 64 + '"}',
                updated_at="2026-10-04",
            )
        )
        database.flush()
        database.add(
            CommunityPublicationRow(
                operation_id="pending-publication",
                artifact_bytes=b"original-gzip",
                manifest_bytes=b"original-manifest",
                signature_bytes=b"original-signature",
                manifest_digest="b" * 64,
                signature_digest="c" * 64,
                preview_digest="d" * 64,
                consent_token="e" * 64,
                author_key_id=source_identity.key_id,
                source_kind="scan",
                source_id="audio-fixture",
                cancel_requested=True,
                error_code="network_unavailable",
            )
        )
        database.commit()
    engine.dispose()
    archive = tmp_path / "portable.backup"
    export_backup(
        source_db, source, archive, passphrase="test-only-password" if encrypted else None
    )
    return archive, source_identity, target


SERVICE = "https://community.example"
_RECEIPT = '{"receipt":1}'
_RETIRED_BYTES = (b"\x1f\x8b retired artifact", b'{"manifest":1}', b"\x00signature")


def _add_retired_revision_rows(database):
    """Revision-protocol rows 0037 retired: an import, its target, and its receipt."""
    database.execute(
        text(
            "INSERT INTO retired_community_imports (id, scan_id, service_url, contribution_id, "
            "revision_id, digest, author_id, artifact_type, artifact_bytes, manifest_bytes, "
            "signature_bytes, historical_key_json, provenance_json, event_ids_json, "
            "receipt_json) VALUES ('retired-import', 'imported-fixture', :service, 'root', "
            "'revision', 'sha256:1', 'author', 'filter-set', :artifact, :manifest, :signature, "
            "'{}', '{}', '[]', :receipt)"
        ),
        dict(
            zip(("artifact", "manifest", "signature"), _RETIRED_BYTES, strict=True),
            service=SERVICE,
            receipt=_RECEIPT,
        ),
    )
    database.execute(
        text(
            "INSERT INTO retired_community_import_targets VALUES "
            "('retired-import', 'media-fixture', '', '/private/movie.mkv', NULL, '{}', 0)"
        )
    )
    database.execute(
        text(
            "INSERT INTO retired_community_operations (operation_id, service_url, kind, state, "
            "receipt, scan_id, updated_at, retired_at, retired_reason) VALUES "
            "('retired-receipt', :service, 'receipt', 'acknowledged', :receipt, "
            "'imported-fixture', '2026-10-01', '2026-10-02', 'revision_protocol_removed')"
        ),
        {"service": SERVICE, "receipt": _RECEIPT},
    )
    database.commit()


def _active_hashes(target):
    return {
        str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target.rglob("*")
        if path.is_file()
    }


def test_full_restore_stages_pending_operation_without_new_remote_ids(tmp_path):
    _, source_identity, target = _fixture(tmp_path)
    source = tmp_path / "source"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, ?, ?, ?)",
            (
                "https://pending.example",
                "contributor-1",
                source_identity.installation_id,
                source_identity.key_id,
            ),
        )
        database.execute(
            "INSERT INTO community_operations (operation_id, service_url, kind, state, "
            "contributor_id, installation_id, contribution_id, revision_id, digest, "
            "receipt, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "retry-1",
                "https://pending.example",
                "submission",
                "submitted",
                "contributor-1",
                source_identity.installation_id,
                "contribution-1",
                "revision-1",
                "sha256:123",
                "signed-receipt",
                "2026-09-27",
            ),
        )
    archive = tmp_path / "pending.backup"
    export_backup(source / "scanner.db", source, archive)
    with preview_restore(archive, target) as preview:
        with sqlite3.connect(preview.stage / "database.sqlite3") as database:
            assert database.execute(
                "SELECT operation_id, state, contribution_id, receipt FROM community_operations "
                "WHERE operation_id='retry-1'"
            ).fetchall() == [("retry-1", "submitted", "contribution-1", "signed-receipt")]


@pytest.mark.parametrize("encrypted", [False, True])
def test_preview_stages_all_work_without_leaking_secrets_or_touching_target(tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age transport not installed")
    archive, source_identity, target = _fixture(tmp_path, encrypted=encrypted)
    before = _active_hashes(target)
    kwargs = {"passphrase": "test-only-password"} if encrypted else {}
    with preview_restore(archive, target, **kwargs) as preview:
        plan = preview.plan
        assert set(plan) == {
            "format",
            "kind",
            "protection",
            "source_schema",
            "staged_schema",
            "source_identity",
            "account_identities",
            "omitted_environment",
            "included_secret_types",
            "administrator_included",
            "backup_deployment",
        }
        assert plan["kind"] == "full"
        assert plan["protection"] == ("age-scrypt" if encrypted else "plain")
        assert plan["source_identity"]["key_id"] == source_identity.key_id
        assert "private-canary" not in json.dumps(plan)
        assert "test-only-password" not in json.dumps(plan)
        with sqlite3.connect(preview.stage / "database.sqlite3") as database:
            assert database.execute("SELECT api_key FROM jellyfin_settings").fetchone() == (
                "private-canary",
            )
            rows = database.execute("SELECT confirmed_identity_json FROM media_items").fetchall()
            assert json.loads(rows[0][0])["identifiers"] == [
                {
                    "provider": "tmdb",
                    "entity_type": "movie",
                    "value": "501",
                    "provenance": "user_confirmed",
                }
            ]
            memory = database.execute(
                "SELECT selection_json FROM audio_track_selections"
            ).fetchall()
            provenance = database.execute(
                "SELECT requested_audio_selection_json, analyzed_audio_selection_json FROM scans"
            ).fetchall()
            assert json.loads(memory[0][0])["track"]["title"] == "Main"
            assert provenance[0][0] == provenance[0][1] == memory[0][0]
            imported = database.execute(
                "SELECT artifact_bytes, manifest_bytes, signature_bytes, historical_key_json, "
                "provenance_json, event_ids_json, receipt_json "
                "FROM retired_community_imports"
            ).fetchall()
            targets = database.execute(
                "SELECT import_id, media_item_id, media_source_id, target_evidence_json "
                "FROM retired_community_import_targets"
            ).fetchall()
            receipts = database.execute(
                "SELECT operation_id, receipt, scan_id, retired_reason "
                "FROM retired_community_operations"
            ).fetchall()
            votes = database.execute(
                "SELECT operation_id, state, community_version_id, filter_id, vote, receipt_id, "
                "contributor_id FROM community_operations WHERE kind='filter_vote'"
            ).fetchall()
            assert votes == [
                ("vote-backup-up", "submitted", "v_backup", "f_backup", "up", "b" * 64, "reader")
            ]
            assert imported[0][:3] == _RETIRED_BYTES
            assert all(imported[0][3:])
            assert targets == [("retired-import", "media-fixture", "", "{}")]
            assert receipts == [
                (
                    "retired-receipt",
                    imported[0][-1],
                    "imported-fixture",
                    "revision_protocol_removed",
                )
            ]
            publications = database.execute(
                "SELECT p.operation_id, p.artifact_bytes, p.manifest_bytes, p.signature_bytes, "
                "p.cancel_requested, p.error_code, o.state, o.revision_id "
                "FROM community_publications p JOIN community_operations o USING (operation_id)"
            ).fetchall()
            assert publications == (
                [
                    (
                        "pending-publication",
                        b"original-gzip",
                        b"original-manifest",
                        b"original-signature",
                        1,
                        "network_unavailable",
                        "submitted",
                        "original-submission",
                    )
                ]
            )
        staging = preview.stage
    assert not staging.exists()
    assert _active_hashes(target) == before
