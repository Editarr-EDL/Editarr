"""Exercise the MCF 1.1.1 vocabulary through registry, import and review download."""

import json

import scanner.api as root
from scanner.http import review_export_api, scan_catalog_api
from scanner.mcf import parse_mcf_content
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app

__all__ = ["app"]

CLASSIFICATIONS = {
    "gore": ("violence", "Gore"),
    "abuse": ("violence", "Abuse"),
    "rape": ("violence", "Rape"),
    "innuendo": ("language", "Innuendo"),
    "sexualRape": ("sex", "Rape"),
    "sizeism": ("discrimination", "Sizeism"),
    "ableism": ("discrimination", "Ableism"),
    "ageism": ("discrimination", "Ageism"),
    "other": ("dispensable_scenes", "Other"),
}


def test_mcf_111_registry_import_and_download(app, monkeypatch):
    app.client.app.include_router(scan_catalog_api.router)
    app.client.app.include_router(review_export_api.router)
    registry = app.send("GET", "/api/mcf-registry", app.accounts.cookie(USER))
    assert registry.status_code == 200, registry.text
    registered = {
        category["token"]: (family["token"], category["label"])
        for family in registry.json()["families"]
        for category in family["categories"]
    }
    assert {token: registered[token] for token in CLASSIFICATIONS} == CLASSIFICATIONS

    item_id = app.jellyfin_item(guid("extended-mcf"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    header = (
        "WEBVTT MovieContentFilter 1.1.1\n\nNOTE\nTITLE Example Movie\nYEAR 2000\nTYPE movie\n"
        "\nNOTE\nSTART 00:00:00.000\nEND 00:01:00.000\n\n"
    )
    cues = "\n\n".join(
        f"00:00:{i:02}.000 --> 00:00:{i + 1:02}.000\n{token}=medium=both"
        for i, token in enumerate(CLASSIFICATIONS, 1)
    )
    options = {
        "target": {"source": "jellyfin", "item_id": item_id},
        "name": "Extended MCF",
        "synchronization": {"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"},
        "mcf_material_end_seconds": 60,
    }
    headers = app.accounts.unsafe_cookie(USER)
    uploaded = app.client.post(
        "/api/mcf-imports/upload",
        headers=headers,
        data={"options": json.dumps(options)},
        files={"file": ("extended.mcf", (header + cues + "\n").encode())},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["source_version"] == body["scan"]["mcf_document"]["source_version"] == "1.1.1"
    review = body["review"]
    expected = {token: family for token, (family, _) in CLASSIFICATIONS.items()}
    assert {cue["subcategory"]: cue["category"] for cue in review["filters"]} == expected

    downloaded = app.send(
        "POST",
        f"/api/reviews/{review['id']}/exports/mcf/download",
        headers,
        {key: review[key] for key in ("revision", "policy_owner_id", "policy_revision")},
    )
    assert downloaded.status_code == 200, downloaded.text
    exported = parse_mcf_content(downloaded.content)
    assert exported.source_version.value == "1.1.1"
    assert {cue.subcategory.value: cue.category.value for cue in exported.entries} == expected
