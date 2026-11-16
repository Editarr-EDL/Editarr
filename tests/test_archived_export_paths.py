# ruff: noqa: F811
"""An archived export still owns the path it records.

Cleanup, recovery, and new publications decide from every export row, archived or
live, so no file an archived export records is removed or claimed by a live one.
"""

from __future__ import annotations

import errno

import pytest
from sqlalchemy import text

from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    link_app,  # noqa: F401
    prove_and_link,
    publications,
    publish_both,
    transitions,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, publish, review_app  # noqa: F401


def _recover(app):
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _archive_item_of(app, export_id: int) -> None:
    with app.accounts.factory() as db:
        db.execute(
            text(
                "UPDATE media_items SET archived_source = 'radarr' WHERE id = "
                "(SELECT media_item_id FROM media_item_exports WHERE id = :id)"
            ),
            {"id": export_id},
        )
        db.commit()


def _record(app, export_id: int, path) -> None:
    with app.accounts.factory() as db:
        db.execute(
            text("UPDATE media_item_exports SET destination_path = :path WHERE id = :id"),
            {"path": str(path), "id": export_id},
        )
        db.commit()


def test_link_recovery_keeps_the_old_file_of_an_archived_export_whose_new_copy_is_gone(
    link_app, monkeypatch
):
    publish_both(link_app, USER, "archived-recovery")
    before = publications(link_app, USER)
    real = files.remove_old

    def unavailable(path, expected):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(files, "remove_old", unavailable)
    assert prove_and_link(link_app, ADMIN, USER, "Drew").json()["state"] == "removing"
    moved = publications(link_app, USER)
    for _id, _format, _export, new_path, _content, _revision in moved:
        new_path.unlink()
    _archive_item_of(link_app, before[0][0])
    monkeypatch.setattr(files, "remove_old", real)
    _recover(link_app)
    assert transitions(link_app, USER) == [("completed", None, ["kept", "kept"])]
    for *_, old_path, content, _revision in before:
        assert old_path.read_bytes() == content


def test_returned_old_bytes_an_archived_export_records_stay(link_app):
    publish_both(link_app, USER, "archived-returned")
    before = publications(link_app, USER)
    before[0][3].write_bytes(b"edited by hand\n")
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    assert transitions(link_app, USER) == [("completed", None, ["kept", "removed"])]
    old_path, content = before[0][3], before[0][4]
    old_path.write_bytes(content)
    _record(link_app, before[0][0], old_path)
    _archive_item_of(link_app, before[0][0])
    _recover(link_app)
    assert old_path.read_bytes() == content


@pytest.mark.parametrize(
    ("source", "server"), [("radarr", "radarr:copy"), ("manual_files", "editarr-manual")]
)
def test_a_live_publication_never_claims_the_path_an_archived_export_records(app, source, server):
    from tests.test_personal_exports_http import seeded_review

    review = seeded_review(app, USER, "archived-collision")
    assert publish(app, review, "edl").status_code == 200
    [(export_id, _format, _export, path, _content, _revision)] = publications(app, USER)
    path.unlink()
    with app.accounts.factory() as db:
        columns = [row[1] for row in db.execute(text("PRAGMA table_info(media_items)"))]
        copied = ", ".join(
            {"id": "'archived-copy'", "server_id": ":server"}.get(column, column)
            for column in columns
        )
        db.execute(
            text(
                f"INSERT INTO media_items ({', '.join(columns)}) SELECT {copied} "
                "FROM media_items WHERE id = "
                "(SELECT media_item_id FROM media_item_exports WHERE id = :id)"
            ),
            {"id": export_id, "server": server},
        )
        db.execute(
            text("UPDATE media_item_exports SET media_item_id = 'archived-copy' WHERE id = :id"),
            {"id": export_id},
        )
        db.execute(
            text("UPDATE media_items SET archived_source = :source WHERE id = 'archived-copy'"),
            {"source": source},
        )
        db.commit()
    refused = publish(app, review, "edl")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "export_filename_collision"
    assert "archived record" in refused.json()["detail"]["message"]
    assert not path.exists()
