"""Builder-generated artifact v2 cases and hostile source/contract boundaries."""

import base64
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import event
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityImportRow,
    ReviewSynchronizationRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
    ScanStageCoverageRow,
)
from scanner.formats.community.codec import ArtifactError, parse_artifact, parse_manifest
from scanner.integrations.community.donation_sources import SourceRef, canonical, capture_source
from scanner.integrations.community.donations import approve_donation, prepare_donation
from scanner.security.community_artifact_signatures import signature_base
from tests.test_community_donations import BOUNDS, audio_selection, options, seed_mixed_review
from tests.test_community_donations import source_database as source_database

CASES = ("ai", "manual", "imported", "derived", "retimed", "partial", "legacy", "zero")
TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def prepare_case(engine, case):
    """Create each public case from persisted test rows through the actual builder."""
    reference = SourceRef("scan", "scan")
    changes = {"submission_id": f"submission-{case}", "name": f"Public {case}"}
    if case in {"derived", "retimed"}:
        seed_mixed_review(engine)
        reference = SourceRef("review", "review")
        changes.update(
            duration_seconds=20 if case == "retimed" else 10, audio_source_scan_id="scan"
        )
        with Session(engine) as db:
            if case == "derived":
                db.query(ScanMcfDocumentRow).delete()
                db.get(ReviewSynchronizationRow, "review").end_seconds = 10
            db.commit()
    else:
        with Session(engine) as db:
            scan = db.get(ScanRow, "scan")
            if case in {"manual", "imported"}:
                scan.origin = case
            if case == "imported":
                db.add(
                    CommunityImportRow(
                        id="import",
                        scan_id="scan",
                        service_url="https://private-provider.example/?token=secret",
                        contribution_id="original",
                        revision_id="original-revision",
                        digest="a" * 64,
                        author_id="original-author",
                        artifact_type="scan",
                    )
                )
            if case in {"ai", "partial"}:
                scan.analyzed_audio_selection_json = audio_selection()
                scan.release_version = "test-release"
                scan.scanner_behavior_version = "test-scanner"
                if case == "partial":
                    scan.status = "failed"
                db.add(
                    ScanStageCoverageRow(
                        scan_id="scan",
                        stage="audio_scan",
                        execution_id="PRIVATE EXECUTION TOKEN",
                        status="partial" if case == "partial" else "complete",
                        source="generated",
                        release_version="test-release",
                        scanner_behavior_version="test-scanner",
                        model_id="test-model",
                        model_revision="test-revision",
                        effective_settings_json=json.dumps(
                            {
                                "audio_model": "test-model",
                                "audio_filter": "test-filter",
                                "api_key": "PRIVATE SECRET",
                            }
                        ),
                        intervals_json=json.dumps(
                            [{"start_seconds": 0, "end_seconds": 5 if case == "partial" else 10}]
                        ),
                    )
                )
            if case == "zero":
                db.query(ScanFindingRow).delete()
            db.commit()
    return prepare_donation(engine, reference, options(**changes), bounds=BOUNDS, source_limit=4)


def vector(prepared, engine):
    signed = approve_donation(
        engine, prepared, consent_token=prepared.consent_token, private_key=TEST_KEY
    )
    return {
        "artifact": base64.b64encode(signed.artifact_bytes).decode("ascii"),
        "manifest": base64.b64encode(signed.manifest_bytes).decode("ascii"),
        "envelope": base64.b64encode(signed.envelope_bytes).decode("ascii"),
    }


@pytest.mark.parametrize("case", CASES)
def test_builder_cases_sign_round_trip(source_database, case):
    prepared = prepare_case(source_database, case)
    stored = json.loads(canonical(vector(prepared, source_database)))
    artifact_bytes = base64.b64decode(stored["artifact"])
    manifest_bytes = base64.b64decode(stored["manifest"])
    decoded = parse_artifact(artifact_bytes, bounds=BOUNDS, compressed=True)
    manifest = parse_manifest(manifest_bytes, artifact=decoded, bounds=BOUNDS)
    envelope = json.loads(base64.b64decode(stored["envelope"]))
    TEST_KEY.public_key().verify(
        base64.b64decode(envelope["signature"]), signature_base(manifest_bytes, manifest.manifest)
    )
    assert artifact_bytes == prepared.artifact_bytes and manifest_bytes == prepared.manifest_bytes
    assert decoded.artifact.schema_version == "community-artifact-2"
    assert b"PRIVATE" not in prepared.preview_bytes
    assert b"private-provider" not in prepared.preview_bytes
    if case == "partial":
        assert decoded.summary["source_states"][0]["status"] == "failed"
    if case in {"ai", "partial"}:
        assert decoded.artifact.provenance.stages[0].settings["audio_filter"] == "test-filter"
    if case == "zero":
        assert decoded.summary["total"] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(source_revision=-1),
        lambda item: item.update(audio_association="unknown"),
        lambda item: item["sources"][0].update(status="running"),
        lambda item: item["sources"].append(item["sources"][0].copy()),
        lambda item: item["sources"][0].update(origin="manual"),
        lambda item: item["analyzed_audio"].update(language="fr"),
        lambda item: item["analyzed_audio"].update(stream_index=1),
        lambda item: item["findings"][0].update(source_snapshot_id="missing"),
        lambda item: item["sources"][0]["provenance"]["stages"][0]["settings"].update(
            api_key="private"
        ),
    ],
)
def test_v2_rejects_malformed_claims(source_database, mutate):
    prepared = prepare_case(source_database, "ai")
    artifact = json.loads(gzip.decompress(prepared.artifact_bytes))
    mutate(artifact)
    with pytest.raises(ArtifactError):
        parse_artifact(canonical(artifact), bounds=BOUNDS)


def test_v2_rejects_retiming_and_manifest_version_substitution(source_database):
    prepared = prepare_case(source_database, "retimed")
    artifact = json.loads(gzip.decompress(prepared.artifact_bytes))
    artifact["retiming"][0]["applied"] = False
    with pytest.raises(ArtifactError):
        parse_artifact(canonical(artifact), bounds=BOUNDS)
    manifest = json.loads(prepared.manifest_bytes)
    manifest["artifact_schema_version"] = "community-artifact-1"
    with pytest.raises(ArtifactError):
        parse_manifest(
            canonical(manifest),
            artifact=parse_artifact(prepared.artifact_bytes, bounds=BOUNDS, compressed=True),
            bounds=BOUNDS,
        )


def test_read_snapshot_is_consistent_during_concurrent_wal_edit(source_database):
    with source_database.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    changed = False

    def change_after_initial_reads(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and statement.startswith("SELECT scan_stage_coverage"):
            changed = True
            with Session(source_database) as writer:
                writer.query(ScanFindingRow).one().end_seconds = 3
                writer.commit()

    event.listen(source_database, "before_cursor_execute", change_after_initial_reads)
    try:
        old = capture_source(
            source_database, SourceRef("scan", "scan"), bounds=BOUNDS, source_limit=4
        )
    finally:
        event.remove(source_database, "before_cursor_execute", change_after_initial_reads)
    assert changed and old.data()["findings"][0]["end_seconds"] == 2
    new = capture_source(source_database, SourceRef("scan", "scan"), bounds=BOUNDS, source_limit=4)
    assert new.data()["findings"][0]["end_seconds"] == 3 and old.guard != new.guard


def test_cancelled_preparation_cannot_yield_signable_bytes(source_database):
    with pytest.raises(ArtifactError, match="cancelled"):
        prepare_donation(
            source_database,
            SourceRef("scan", "scan"),
            options(),
            bounds=BOUNDS,
            source_limit=4,
            should_cancel=lambda: True,
        )


@pytest.mark.parametrize("case", CASES)
def test_frozen_builder_bytes_and_signatures(source_database, case):
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/community/donation-artifacts-v2.json").read_text()
    )["cases"][case]
    prepared = prepare_case(source_database, case)
    assert vector(prepared, source_database) == frozen
    assert prepared.artifact_bytes[9] == 255


def test_source_changes_during_preparation_require_new_preview(source_database, monkeypatch):
    from scanner.integrations.community import donations

    original = donations.project_source

    def change_after_projection(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(source_database) as writer:
            writer.query(ScanFindingRow).one().end_seconds = 3
            writer.commit()
        return result

    monkeypatch.setattr(donations, "project_source", change_after_projection)
    with pytest.raises(ArtifactError, match="Source changed during preparation"):
        donations.prepare_donation(
            source_database, SourceRef("scan", "scan"), options(), bounds=BOUNDS, source_limit=4
        )


@pytest.mark.parametrize(
    "reason,allowed",
    [
        ("audio_pcm_unavailable", True),
        ("read_budget_exceeded", False),
        ("source_changed", False),
    ],
)
def test_only_known_unsupported_formats_can_omit_audio_digest(
    source_database, tmp_path, monkeypatch, reason, allowed
):
    from scanner.integrations.community import donations
    from scanner.integrations.community.stream_fingerprints import StreamUnsupported

    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"synthetic test media placeholder")
    with Session(source_database) as db:
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(path)
        scan.analyzed_audio_selection_json = audio_selection()
        db.commit()

    def unavailable(*args, **kwargs):
        raise StreamUnsupported(reason)

    monkeypatch.setattr(donations, "fingerprint_pcm_audio", unavailable)
    if not allowed:
        with pytest.raises(ArtifactError, match="could not be captured safely"):
            donations.prepare_donation(
                source_database, SourceRef("scan", "scan"), options(), bounds=BOUNDS, source_limit=4
            )
    else:
        prepared = donations.prepare_donation(
            source_database, SourceRef("scan", "scan"), options(), bounds=BOUNDS, source_limit=4
        )
        preview = json.loads(prepared.preview_bytes)
        assert preview["artifact"]["matching"]["audio_stream"] is None
        assert preview["artifact"]["audio_association"] == "identified"
        assert "audio_fingerprint_unsupported" in preview["warnings"]


def generate_vectors(destination: Path):
    """Regenerate synthetic golden bytes only when this module is explicitly invoked."""
    cases = {}
    for case in CASES:
        with TemporaryDirectory() as directory:
            fixture = source_database.__wrapped__(Path(directory))
            engine = next(fixture)
            try:
                cases[case] = vector(prepare_case(engine, case), engine)
            finally:
                next(fixture, None)
    destination.write_text(
        json.dumps(
            {
                "public_key": base64.b64encode(TEST_KEY.public_key().public_bytes_raw()).decode(
                    "ascii"
                ),
                "cases": cases,
            },
            indent=2,
        )
        + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate synthetic donation v2 golden fixtures")
    parser.add_argument("destination", type=Path)
    generate_vectors(parser.parse_args().destination)
