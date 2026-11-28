import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scanner.formats.community.artifact_v2 import without_arr_provenance
from scanner.formats.community.codec import (
    ArtifactError,
    ResourceBounds,
    parse_artifact,
    parse_manifest,
)
from scanner.models import MCF_CATEGORY_REGISTRY

BOUNDS = ResourceBounds(compressed_bytes=4096, expanded_bytes=16384, events=10, text=256, depth=12)
# The retired v1 artifact, kept here since the service no longer ships it.
FIXTURE = Path(__file__).resolve().parent / "fixtures/community/artifact-v1.json"
TAXONOMY = Path(__file__).resolve().parents[1] / "community-service/src/artifact/taxonomy.json"


def artifact() -> dict:
    return {
        "schema_version": "community-artifact-1",
        "submission_id": "submission-1",
        "kind": "scan",
        "created_at": "2026-09-26T00:00:00Z",
        "author": {"contributor_id": "contributor-1", "key_id": "key-1"},
        "sharing_terms": None,
        "media": {
            "duration_seconds": 12,
            "timeline": "source_file",
            "time_unit": "seconds",
            "work_ids": [],
        },
        "provenance": {"release_version": None, "scanner_behavior_version": None, "stages": []},
        "coverage": [
            {"channel": "audio", "capability": "language", "state": "unknown", "intervals": []}
        ],
        "findings": [],
        "derivation": None,
    }


def test_unknown_zero_finding_artifact_preserves_exact_bytes():
    raw = json.dumps(artifact(), ensure_ascii=False).encode("utf-8")
    result = parse_artifact(raw, bounds=BOUNDS)
    assert result.stored_bytes == raw
    assert result.summary["total"] == 0
    assert result.summary["coverage"][0]["state"] == "unknown"


def test_out_of_timeline_finding_is_rejected():
    payload = artifact()
    payload["findings"] = [
        {
            "event_id": "event-1",
            "start_seconds": 11,
            "end_seconds": 13,
            "source": "audio",
            "channel": "audio",
            "category": "language",
            "subcategory": "swearing",
            "severity": "medium",
            "confidence": None,
            "action": None,
            "note": "previewed note",
            "parent_event_id": None,
        }
    ]
    with pytest.raises(ArtifactError, match="timeline"):
        parse_artifact(json.dumps(payload).encode("utf-8"), bounds=BOUNDS)


def test_shared_fixture_counts_and_registry_parity():
    compact = json.dumps(
        json.loads(FIXTURE.read_text()), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert len(compact) == 1023
    assert hashlib.sha256(compact).hexdigest() == (
        "1b23acc6ba8722de0e3a518437ed3f5ea770045aa40f10b80d44e84e07ad7c21"
    )
    expected_registry = {
        family.value: [token for token, _ in entries]
        for family, entries in MCF_CATEGORY_REGISTRY.items()
    }
    assert json.loads(TAXONOMY.read_text()) == expected_registry
    raw = FIXTURE.read_bytes()
    result = parse_artifact(raw, bounds=BOUNDS)
    assert result.stored_bytes == raw
    assert result.summary["total"] == 2
    assert result.summary["channels"] == {"audio": 1, "video": 2}
    assert result.summary["taxonomy"] == {"language/swearing": 1, "unclassified": 1}
    assert result.summary["work_ids"] == [
        {"provider": "tmdb", "entity_type": "movie", "value": "123"}
    ]


def test_manual_derived_and_partial_artifacts():
    payload = json.loads(FIXTURE.read_text())
    payload["coverage"][0]["state"] = "partial"
    payload["coverage"][0]["intervals"] = [{"start_seconds": 1, "end_seconds": 5}]
    payload["provenance"]["stages"] = [
        {
            "execution_id": "attempt-1",
            "capability": "language",
            "state": "partial",
            "model_id": None,
            "model_revision": None,
            "model_digest": None,
            "settings": {"audio_model": "whisper"},
            "intervals": [{"start_seconds": 1, "end_seconds": 5}],
        }
    ]
    assert (
        parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS).summary["coverage"][0]["state"]
        == "partial"
    )
    payload["kind"] = "manual"
    payload["provenance"]["stages"] = []
    payload["findings"] = [payload["findings"][1]]
    assert parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS).summary["total"] == 1
    payload["kind"] = "derived"
    payload["media"]["timeline"] = "derived_target"
    payload["findings"][0]["source"] = "derived"
    payload["findings"][0]["parent_event_id"] = "parent-event"
    payload["derivation"] = {
        "parents": [{"submission_id": "parent", "digest": "a" * 64}],
        "transform": "retimed",
    }
    assert parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS).summary["sources"] == {
        "derived": 1
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(schema_version="community-artifact-2"),
        lambda item: item["findings"][0].update(confidence=float("nan")),
        lambda item: item["findings"][0].update(subcategory="murder"),
        lambda item: item["findings"][0].update(subcategory="innuendo"),
        lambda item: item["findings"][0].update(end_seconds=13),
        lambda item: item["findings"][1].update(event_id="event-1"),
        lambda item: item["coverage"][0].update(
            intervals=[{"start_seconds": 4, "end_seconds": 15}]
        ),
        lambda item: item["provenance"].update(
            stages=[
                {
                    "execution_id": "attempt-1",
                    "capability": "audio",
                    "state": "partial",
                    "model_id": None,
                    "model_revision": None,
                    "model_digest": None,
                    "settings": {"secret": "not public"},
                    "intervals": [],
                }
            ]
        ),
    ],
)
def test_invalid_artifact_is_rejected(mutate):
    payload = json.loads(FIXTURE.read_text())
    mutate(payload)
    with pytest.raises(ArtifactError):
        parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS)


def test_duplicate_keys_and_compression_bomb_are_rejected():
    with pytest.raises(ArtifactError, match="Duplicate"):
        parse_artifact(
            FIXTURE.read_bytes().replace(
                b'"submission_id": "submission-1",',
                b'"submission_id": "submission-1", "submission_id": "other",',
                1,
            ),
            bounds=BOUNDS,
        )
    compressed = gzip.compress(FIXTURE.read_bytes())
    assert parse_artifact(compressed, bounds=BOUNDS, compressed=True).stored_bytes == compressed
    with pytest.raises(ArtifactError, match="Expanded"):
        parse_artifact(compressed, bounds=ResourceBounds(4096, 100, 10, 256, 12), compressed=True)


def test_manifest_binds_exact_gzip_bytes_identity_and_recomputed_summary():
    compressed = gzip.compress(FIXTURE.read_bytes())
    artifact_result = parse_artifact(compressed, bounds=BOUNDS, compressed=True)
    manifest = {
        "schema_version": "community-manifest-1",
        "artifact_format": "gzip-json",
        "artifact_schema_version": "community-artifact-1",
        "artifact_digest": hashlib.sha256(compressed).hexdigest(),
        "submission_id": "submission-1",
        "contributor_id": "contributor-1",
        "key_id": "key-1",
        "summary": artifact_result.summary,
    }
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    assert parse_manifest(raw, artifact=artifact_result, bounds=BOUNDS).stored_bytes == raw
    for key, altered in [
        ("artifact_digest", "0" * 64),
        ("contributor_id", "another"),
        ("summary", {**artifact_result.summary, "total": 200}),
        ("summary", {**artifact_result.summary, "channels": {"audio": True, "video": 2}}),
    ]:
        with pytest.raises(ArtifactError, match="does not match"):
            parse_manifest(
                json.dumps({**manifest, key: altered}).encode(),
                artifact=artifact_result,
                bounds=BOUNDS,
            )
    with pytest.raises(ArtifactError, match="requires gzip"):
        parse_manifest(
            raw, artifact=parse_artifact(FIXTURE.read_bytes(), bounds=BOUNDS), bounds=BOUNDS
        )


def test_shared_exact_gzip_vector_and_content_bounds():
    vector = json.loads(FIXTURE.with_name("artifact-v1-gzip.json").read_text())
    compressed = base64.b64decode(vector["base64"])
    assert hashlib.sha256(compressed).hexdigest() == vector["sha256"]
    compact = json.dumps(
        json.loads(FIXTURE.read_text()), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert gzip.compress(compact, mtime=0) == compressed
    assert parse_artifact(compressed, bounds=BOUNDS, compressed=True).summary["total"] == 2
    payload = json.loads(FIXTURE.read_text())
    payload["findings"][0]["note"] = "x" * 257
    with pytest.raises(ArtifactError, match="text"):
        parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS)
    payload = json.loads(FIXTURE.read_text())
    payload["findings"][0]["transcript"] = "private text"
    with pytest.raises(ArtifactError, match="transcript"):
        parse_artifact(json.dumps(payload).encode(), bounds=BOUNDS)
    with pytest.raises(ArtifactError, match="depth"):
        parse_artifact(FIXTURE.read_bytes(), bounds=ResourceBounds(4096, 16384, 10, 256, 2))


def test_artifact_signed_with_arr_provenance_reads_as_migration_0017_values():
    vectors = Path(__file__).parent / "fixtures/community/donation-artifacts-v2.json"
    case = json.loads(vectors.read_text())["cases"]["legacy"]
    value = json.loads(gzip.decompress(base64.b64decode(case["artifact"])))
    seconds = value["media"]["duration_seconds"]
    value["matching"]["media"]["release"]["duration"] = {
        "seconds": seconds,
        "source": "radarr_catalog_minutes",
        "precision_seconds": 60,
    }
    value["matching"]["media"]["identifiers"][0]["provenance"] = "radarr.movie"
    stored = gzip.compress(json.dumps(value).encode(), mtime=0)
    manifest = json.loads(base64.b64decode(case["manifest"]))
    manifest["artifact_digest"] = hashlib.sha256(stored).hexdigest()
    manifest["summary"]["matching"] = value["matching"]
    bounds = ResourceBounds(
        compressed_bytes=65536, expanded_bytes=524288, events=100, text=4096, depth=24
    )

    decoded = parse_artifact(stored, bounds=bounds, compressed=True)
    parse_manifest(json.dumps(manifest).encode(), artifact=decoded, bounds=bounds)

    duration = decoded.artifact.matching["media"]["release"]["duration"]
    assert duration == {"seconds": seconds, "source": "jellyfin_ticks", "precision_seconds": 60}
    assert decoded.artifact.matching["media"]["identifiers"][0]["provenance"] == "source_metadata"
    assert decoded.summary["matching"] == decoded.artifact.matching
    assert decoded.stored_bytes == stored


def test_arr_provenance_mapping_matches_community_migration_0017():
    identifier = {"provider": "tvdb", "entity_type": "series", "value": "81189"}
    matching = {
        "media": {
            "identifiers": [{**identifier, "provenance": "Radarr.movie"}],
            "series_identifiers": [
                {**identifier, "provenance": "sonarr.series"},
                {**identifier, "provenance": "user_confirmed"},
            ],
            "release": {"duration": {"seconds": 10, "source": "sonarr_catalog_minutes"}},
        }
    }

    media = without_arr_provenance(matching)["media"]

    assert [item["provenance"] for item in media["identifiers"]] == ["source_metadata"]
    assert [item["provenance"] for item in media["series_identifiers"]] == [
        "source_metadata",
        "user_confirmed",
    ]
    assert media["release"]["duration"]["source"] == "jellyfin_ticks"
    assert matching["media"]["identifiers"][0]["provenance"] == "Radarr.movie"
    assert without_arr_provenance({"media": {"identifiers": []}}) == {"media": {"identifiers": []}}


def test_arr_provenance_mapping_folds_case_like_the_community_service():
    identifier = {"provider": "tmdb", "entity_type": "movie", "value": "603"}
    provenances = ["SONARR.x", "Radarr.x", "\u017fonarr.x"]
    matching = {
        "media": {"identifiers": [{**identifier, "provenance": value} for value in provenances]}
    }

    media = without_arr_provenance(matching)["media"]

    assert [item["provenance"] for item in media["identifiers"]] == [
        "source_metadata",
        "source_metadata",
        "\u017fonarr.x",
    ]
