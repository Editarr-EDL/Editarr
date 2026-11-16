# ruff: noqa: F811
"""DELETE of a publication whose media folder cannot be resolved is a handled refusal.

A symbolic-link loop (ELOOP) or a file in place of the folder (ENOTDIR) is not
absence, so the retained path cannot be inspected: the request answers 409
`export_destination_unavailable`, keeps the bytes, row, and state, and succeeds once
the folder is restored. Ports the round 5 review probe for K2
(web/test-results/mu-35-review-5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow
from tests.multi_user_access_support import USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_ownership import delete, relocate
from tests.test_personal_exports_http import app, publish, review_app, seeded_review  # noqa: F401


def _unresolvable(directory: Path, shape: str) -> Path:
    held = directory.with_name(f"{directory.name}-held")
    directory.rename(held)
    if shape == "loop":
        directory.symlink_to(directory.name)
    else:
        directory.write_bytes(b"not a folder")
    return held


@pytest.mark.parametrize("export_format", ["edl", "mcf"])
@pytest.mark.parametrize("shape", ["loop", "file"])
def test_delete_refuses_an_unresolvable_media_folder_and_keeps_everything(
    app, export_format, shape
):
    review = seeded_review(app, USER, f"delete-{shape}")
    relocate(app, review, Path("share"))
    response = publish(app, review, export_format)
    assert response.status_code == 200, response.text
    export_id = response.json()["id"]
    before, content, path = observe(app, export_id)
    share = path.parents[2]
    held = _unresolvable(share, shape)

    refused = delete(app, export_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "export_destination_unavailable"
    assert (held / path.relative_to(share)).read_bytes() == content
    assert observe(app, export_id)[:2] == (before, content)
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        assert row.regeneration is None or not row.regeneration.deleted

    share.unlink()
    held.rename(share)
    assert delete(app, export_id).status_code == 204
    assert not path.exists()
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, export_id) is None
