"""Naming is display metadata across source admission, workers, and provenance."""

import json
from pathlib import Path

import pytest

from scanner import api, repository
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_naming_support import (
    _mcf_import_options,
    upload_import,
)
from tests.scan_naming_support import (
    client_context as client_context,
)
from tests.scan_naming_support import (
    db as db,
)


def test_mcf_import_maps_source_timing_before_target_runtime_validation(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "short-cut.mkv"
    media_path.write_bytes(b"video")
    content = (
        b"WEBVTT MovieContentFilter 1.1.0\n\n"
        b"NOTE\nSTART 00:00:10.000\nEND 00:01:50.000\n\n"
        b"00:00:30.000 --> 00:00:50.000\nviolence=high=video\n"
    )
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 50.0)
    target = jellyfin_target(monkeypatch, db, media_path)

    response = upload_import(
        client,
        "mcf",
        _mcf_import_options("Mapped", target, end_seconds=50),
        content,
        headers=headers,
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["scan"]["mcf_document"]["material_start_seconds"] == 10
    assert payload["scan"]["mcf_document"]["material_end_seconds"] == 110
    assert payload["review"]["synchronization"] == {
        "start_seconds": 0,
        "end_seconds": 50,
        "end_mode": "before_credits",
    }
    assert payload["review"]["filters"][0]["start_seconds"] == 10
    assert payload["review"]["filters"][0]["end_seconds"] == 20
    source = db.query(repository.ScanFindingRow).one()
    assert (source.start_seconds, source.end_seconds) == (30, 50)


@pytest.mark.parametrize(
    "bounds",
    [
        "NOTE\nSTART 00:00:10.000\nEND 00:00:10.000\n\n",
        "NOTE\nSTART 00:00:11.000\nEND 00:00:10.000\n\n",
        "NOTE\nSTART invalid\nEND 00:00:10.000\n\n",
        "",
    ],
)
def test_mcf_import_cannot_ignore_invalid_source_bounds(
    client_context, tmp_path, db, monkeypatch, bounds
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 50.0)
    target = jellyfin_target(monkeypatch, db, media_path)
    content = (
        "WEBVTT MovieContentFilter 1.1.0\n\n"
        + bounds
        + "00:00:01.000 --> 00:00:02.000\nviolence=high=video\n"
    ).encode()

    response = client.post(
        "/api/mcf-imports/upload",
        files={"file": ("invalid-bounds.mcf", content, "text/plain")},
        data={"options": json.dumps(_mcf_import_options("Invalid", target, ignore_errors=True))},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_source_mcf_bounds"
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0


@pytest.mark.parametrize(
    ("target_end", "cue", "expected_code"),
    [
        (51, "00:00:10.000 --> 00:00:20.000", "invalid_synchronization_bounds"),
        (50, "00:02:30.000 --> 00:02:40.000", "mapped_cue_out_of_bounds"),
    ],
)
def test_mcf_import_rejects_invalid_target_or_mapped_cue(
    client_context, tmp_path, db, monkeypatch, target_end, cue, expected_code
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    content = (
        "WEBVTT MovieContentFilter 1.1.0\n\n"
        "NOTE\nSTART 00:00:00.000\nEND 00:01:40.000\n\n"
        f"{cue}\nviolence=high=video\n"
    ).encode()
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 50.0)
    target = jellyfin_target(monkeypatch, db, media_path)

    response = upload_import(
        client,
        "mcf",
        _mcf_import_options("Invalid timing", target, end_seconds=target_end),
        content,
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0


def test_mcf_import_rejects_missing_authoritative_runtime(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "unknown-duration.mkv"
    media_path.write_bytes(b"video")
    content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
    monkeypatch.setattr(api, "get_video_duration", lambda _path: None)
    target = jellyfin_target(monkeypatch, db, media_path)

    response = upload_import(
        client,
        "mcf",
        _mcf_import_options("Unknown runtime", target, end_seconds=100),
        content,
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "media_runtime_unavailable"
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0


def test_mcf_import_failures_are_safe_and_create_nothing(client_context, tmp_path, db, monkeypatch):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 6000.0)
    target = jellyfin_target(monkeypatch, db, media_path)

    unsupported = client.post(
        "/api/mcf-imports/upload",
        files={
            "file": (
                "future.mcf",
                b"WEBVTT MovieContentFilter 2.0.0\n\n",
                "text/mcf+vtt",
            )
        },
        data={"options": json.dumps(_mcf_import_options("Future", target))},
        headers=headers,
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_mcf_version"
    assert unsupported.json()["detail"]["line_number"] == 1

    secret_payload = "violence=high # /private/scanner-host/secret\n"
    malformed = client.post(
        "/api/mcf-imports/upload",
        files={
            "file": (
                "invalid.mcf",
                (
                    "WEBVTT MovieContentFilter 1.1.0\n\n"
                    "NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
                    "not a timing line\n" + secret_payload
                ).encode(),
                "text/plain",
            )
        },
        data={"options": json.dumps(_mcf_import_options("Invalid", target))},
        headers=headers,
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "mcf_import_issues"
    assert malformed.json()["detail"]["line_number"] is None
    assert malformed.json()["detail"]["line_text"] is None
    assert malformed.json()["detail"]["import_issues"] == [
        {
            "code": "invalid_mcf_document",
            "message": "MCF cue timing is invalid",
            "line_number": 7,
            "line_text": "not a timing line",
        }
    ]
    assert "/private/scanner-host/secret" not in malformed.text
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0

    extra_blank = client.post(
        "/api/mcf-imports/upload",
        files={
            "file": (
                "extra-blank.mcf",
                b"WEBVTT MovieContentFilter 1.0.0\n\n"
                b"NOTE\nSTART 00:00:00.000\nEND 00:00:02.000\n\n"
                b"\n"
                b"00:00:00.000 --> 00:00:01.000\nviolence=high\n",
                "text/plain",
            )
        },
        data={"options": json.dumps(_mcf_import_options("Extra blank", target))},
        headers=headers,
    )
    assert extra_blank.status_code == 422
    assert extra_blank.json()["detail"]["code"] == "mcf_import_issues"
    assert extra_blank.json()["detail"]["import_issues"] == [
        {
            "code": "invalid_mcf_document",
            "message": "MCF cue timing is invalid",
            "line_number": 7,
            "line_text": "",
        }
    ]

    overlong_line = "x" * 4_097
    overlong = client.post(
        "/api/mcf-imports/upload",
        files={
            "file": (
                "overlong.mcf",
                (
                    "WEBVTT MovieContentFilter 1.0.0\n\n"
                    "NOTE\nSTART " + overlong_line + "\nEND 00:00:02.000\n\n"
                ).encode(),
                "text/plain",
            )
        },
        data={"options": json.dumps(_mcf_import_options("Overlong", target))},
        headers=headers,
    )
    assert overlong.status_code == 413
    assert overlong.json()["detail"]["code"] == "mcf_import_too_large"
    assert overlong.json()["detail"]["line_number"] == 4
    assert overlong.json()["detail"]["line_text"] is None
    assert overlong_line not in overlong.text

    continued = client.post(
        "/api/mcf-imports/upload",
        files={
            "file": (
                "invalid.mcf",
                (
                    "WEBVTT MovieContentFilter 1.1.0\n\n"
                    "NOTE\nTITLE Movie\nTYPE movie\nSEASON \nEPISODE \n\n"
                    "NOTE\nSTART 00:00:00.000\nEND 99:59:59.999\n\n"
                    "00:00:01.000 --> 00:00:02.000\nviolence=low=both\n\n"
                    "02:00:00.000 --> 02:00:01.000\nswearing=low=audio\n"
                ).encode(),
                "text/plain",
            )
        },
        data={"options": json.dumps(_mcf_import_options("Recovered", target, ignore_errors=True))},
        headers=headers,
    )
    assert continued.status_code == 201, continued.text
    assert continued.json()["review"]["name"] == "Recovered"
    assert continued.json()["scan"]["finding_count"] == 2

    wrong_upload_name = client.post(
        "/api/mcf-imports/upload",
        files={"file": ("invalid.txt", b"content", "text/plain")},
        data={"options": json.dumps(_mcf_import_options("Invalid", target))},
        headers=headers,
    )
    assert wrong_upload_name.status_code == 422
    assert wrong_upload_name.json()["detail"]["code"] == "invalid_mcf_document"

    oversized = client.post(
        "/api/mcf-imports/upload",
        files={"file": ("large.mcf", b"x" * 1_048_577, "text/plain")},
        data={"options": json.dumps(_mcf_import_options("Large", target))},
        headers=headers,
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "mcf_import_too_large"
    assert db.query(repository.ScanRow).count() == 1
    assert db.query(repository.SavedReviewRow).count() == 1


@pytest.mark.parametrize("suffix", ["mcf", "exports/mcf", "exports/mcf/download"])
def test_mcf_preflight_and_export_report_incomplete_legacy_filter(
    client_context, tmp_path, db, monkeypatch, suffix
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 10.0)
    imported = upload_import(
        client,
        "edl",
        {"name": "Legacy", "target": jellyfin_target(monkeypatch, db, media_path)},
        b"1 2 1\n",
        headers=headers,
    ).json()
    review_id = imported["review"]["id"]
    filter_id = imported["review"]["filters"][0]["id"]
    db.get(repository.ReviewMcfDocumentRow, review_id).material_end_seconds = None
    db.commit()

    preflight = client.get(f"/api/reviews/{review_id}/mcf/preflight", headers=headers)
    assert preflight.status_code == 200
    assert [issue["code"] for issue in preflight.json()["issues"]] == [
        "invalid_material_bounds",
        "missing_category",
        "missing_subcategory",
        "missing_severity",
    ]
    blocked = client.post(
        f"/api/reviews/{review_id}/{suffix}",
        json={"revision": 0},
        headers=headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "incomplete_mcf_review"
    assert blocked.json()["detail"]["filter_ids"] == [filter_id]
    assert blocked.json()["detail"]["document_fields"] == ["material_end_seconds"]
    assert not media_path.with_suffix(".mcf").exists()
    assert db.query(repository.MediaItemExportRow).count() == 0


def test_import_boundaries_preserve_safe_structured_errors(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    target = jellyfin_target(monkeypatch, db, media_path)

    secret_path = "/private/scanner-host/secret/movie.mkv"
    nested_validation = upload_import(
        client,
        "edl",
        {
            "name": "Import",
            "target": {"source": "manual", "item_id": "manual:item", "video_path": secret_path},
        },
        b"1 2 1\n",
        headers=headers,
    )
    assert nested_validation.status_code == 400
    assert nested_validation.json()["detail"]["code"] == "jellyfin_item_required"
    assert secret_path not in nested_validation.text
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0

    options = json.dumps({"name": "Import", "target": target})
    upload = client.post(
        "/api/edl-imports/upload",
        files={"file": ("input.txt", b"1 2 1\n", "text/plain")},
        data={"options": options},
        headers=headers,
    )
    assert upload.status_code == 422
    assert upload.json()["detail"]["code"] == "invalid_edl_import"

    malformed_upload = client.post(
        "/api/edl-imports/upload",
        files={"file": ("invalid.edl", b"# heading\nnot an action row\n", "text/plain")},
        data={"options": options},
        headers=headers,
    )
    assert malformed_upload.status_code == 422
    assert malformed_upload.json()["detail"]["code"] == "invalid_edl_import"
    assert malformed_upload.json()["detail"]["line_number"] == 1
    assert malformed_upload.json()["detail"]["line_text"] == "# heading"

    for unsafe_name in ("control\x00.edl", "format\u200b.edl"):
        with pytest.raises(ValueError, match="unsupported characters"):
            api._safe_import_basename(unsafe_name)

    assert (
        upload_import(client, "edl", {"name": "Import", "target": target}, b"1 2 1\n").status_code
        == 401
    )


@pytest.mark.parametrize("kind", ["mcf", "edl"])
def test_import_without_a_jellyfin_item_is_refused_and_writes_nothing(
    client_context, tmp_path, db, kind
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    target = {"source": "manual", "video_path": str(media_path)}
    if kind == "mcf":
        content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
        options = _mcf_import_options("Other file", target)
    else:
        content = b"1 2 1\n"
        options = {"name": "Other file", "target": target}
    response = upload_import(client, kind, options, content, headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "jellyfin_item_required"
    assert response.json()["detail"]["message"] == (
        "This file must belong to a Jellyfin item. "
        "Open its Jellyfin item and import the file there."
    )
    assert str(tmp_path) not in response.text
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0
    assert db.query(repository.MediaItemRow).count() == 0


@pytest.mark.parametrize("kind", ["mcf", "edl"])
@pytest.mark.parametrize("target", [{"source": "jellyfin", "item_id": ""}, {"source": "jellyfin"}])
def test_a_malformed_jellyfin_target_is_invalid_not_refused(client_context, db, kind, target):
    client, headers = client_context
    if kind == "mcf":
        content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
        options = _mcf_import_options("Malformed", target)
    else:
        content = b"1 2 1\n"
        options = {"name": "Malformed", "target": target}
    response = upload_import(client, kind, options, content, headers=headers)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == (
        "invalid_mcf_document" if kind == "mcf" else "invalid_edl_import"
    )
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0


@pytest.mark.parametrize("kind", ["mcf", "edl"])
def test_server_path_import_is_gone(client_context, tmp_path, db, monkeypatch, kind):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    source = tmp_path / f"input.{kind}"
    source.write_bytes(b"1 2 1\n")
    options = {"name": "Path", "target": jellyfin_target(monkeypatch, db, media_path)}

    response = client.post(
        f"/api/{kind}-imports/path",
        json=options | {f"{kind}_path": str(source)},
        headers=headers,
    )

    assert response.status_code == 404, response.text
    assert db.query(repository.ScanRow).count() == 0
    assert db.query(repository.SavedReviewRow).count() == 0
