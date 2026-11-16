# ruff: noqa: F401, F811
"""A server path the resolver refuses affects only its own item, on any host.

The resolver refuses a relative or unrecognized server path (`\\`-rooted,
`///`, a mixed prefix, a UNC path without a share, a stream URL) and, on a
Windows host, a name it would split into folders. Every refusal is the same
per-item outcome: the item has no local path, scoped accounts cannot reach it,
and the owner still sees it. These tests run on the POSIX host flavour unless
they say otherwise.
"""

import ntpath
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from scanner import server_paths
from scanner.db import MediaItemRow, ScanRow
from scanner.models import PathMappingCreate
from scanner.persistence.repository import repository_paths
from scanner.persistence.repository.repository_paths import create_path_mapping
from scanner.security.access.identity import CredentialKind
from scanner.security.access.records import (
    AuditEvent,
    AuditOutcome,
    MediaAllowed,
    MediaDenial,
    MediaDenied,
)
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from scanner.security.jellyfin_accounts.evidence import EvidenceCache
from scanner.security.media_access.service import decide_media, load_subject
from tests import catalog_access_http as fixture
from tests import media_access_support
from tests.catalog_access_http import LOCAL
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    MEDIA,
    MOVIES,
    movie,
    resolve,
)
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_audit_history_http import NOW, get, history
from tests.test_catalog_authorization_http import catalog
from tests.test_community_verified_import import import_database
from tests.test_jellyfin_account_evidence import ScriptedAdapter, clock, linked
from tests.test_operations_access_contracts import start
from tests.test_review_access_http import app as review_app
from tests.test_windows_unrepresentable_paths import _bad_jellyfin_path, _ids, _reread

FAMILY_ITEMS = f"/api/libraries/{FAMILY}/items"
REFUSED_FORMS = [
    "relative/x.mkv",
    "\\jellyfin\\family\\x.mkv",
    "/\\jellyfin\\x.mkv",
    "\\/jellyfin/x.mkv",
    "///jellyfin/family/x.mkv",
    "//server",
    "\\\\server",
    "C:x.mkv",
    "https://stream.test/x.mkv",
]


def _tolerant(client) -> None:
    client._transport.raise_server_exceptions = False


@pytest.mark.parametrize("path", REFUSED_FORMS)
def test_the_resolver_refuses_every_unusable_server_path_with_one_type(import_database, path):
    with Session(import_database) as db:
        with pytest.raises(server_paths.UnresolvablePath):
            repository_paths.resolve_local_path(db, path)


def test_a_host_unnameable_name_is_the_same_refusal(import_database, monkeypatch):
    monkeypatch.setattr(repository_paths, "_HOST_PATHS", ntpath)
    with Session(import_database) as db:
        with pytest.raises(server_paths.UnresolvablePath):
            repository_paths.resolve_local_path(db, "/media/a\\b.mkv")


@pytest.mark.parametrize(
    "path", ["\\jellyfin\\family\\x.mkv", "///jellyfin/family/x.mkv", "https://stream.test/x.mkv"]
)
def test_one_unresolvable_item_leaves_the_rest_of_its_library_listed(catalog, monkeypatch, path):
    before = {
        actor: _ids(catalog.get(actor, FAMILY_ITEMS).json()) for actor in (LOCAL, USER, OWNER)
    }
    assert FAMILY_FILM in before[LOCAL] and FAMILY_FILM in before[USER]
    _bad_jellyfin_path(monkeypatch, FAMILY_FILM, path)
    _tolerant(catalog.client)

    for actor in (LOCAL, USER, OWNER):
        response = catalog.get(actor, FAMILY_ITEMS)
        assert response.status_code == 200, (actor, response.text)
        expected = before[actor] if actor == OWNER else before[actor] - {FAMILY_FILM}
        assert _ids(response.json()) == expected, actor


def test_an_unresolvable_item_elsewhere_on_the_server_leaves_linked_reads_intact(
    catalog, monkeypatch
):
    before = {
        actor: _ids(catalog.get(actor, FAMILY_ITEMS).json()) for actor in (LOCAL, USER, OWNER)
    }
    _bad_jellyfin_path(monkeypatch, FEATURE, "\\jellyfin\\movies\\x.mkv")
    _tolerant(catalog.client)

    for actor in (LOCAL, USER, OWNER):
        response = catalog.get(actor, FAMILY_ITEMS)
        assert response.status_code == 200, (actor, response.text)
        assert _ids(response.json()) == before[actor], actor
    assert catalog.get(USER, f"/api/libraries/{MOVIES}/items").status_code == 404


def test_an_event_with_unresolvable_media_leaves_the_audit_pages_intact(history):
    client, accounts, identifiers = history
    totals = {actor: get(history, actor, "/api/audit").json()["total"] for actor in (OWNER, ADMIN)}
    with accounts.factory() as db:
        db.add(
            MediaItemRow(
                id="bad",
                server_id="http://jellyfin.test",
                jellyfin_id="c3000000000000000000000000000009",
                item_type="Movie",
                name="bad",
                path="\\media\\x.mkv",
            )
        )
        db.flush()
        event = AuditEvent(
            NOW,
            OWNER,
            CredentialKind.SESSION,
            AuditAction.EXPORT_PUBLISH,
            AuditOutcome.SUCCEEDED,
            target_user=USER,
            media_key="bad",
            resource="review-1",
            details={"format": "edl", "target_kind": "personal"},
        )
        bad = stage_event(db, event)
        db.commit()
    _tolerant(client)

    for actor in (OWNER, ADMIN):
        filtered = get(history, actor, "/api/audit", page_size=1, media_key="unrelated")
        assert filtered.status_code == 200, (actor, filtered.text)
        assert filtered.json()["total"] == 0
        assert get(history, actor, f"/api/audit/{identifiers[USER]}").status_code == 200
    assert get(history, OWNER, "/api/audit").json()["total"] == totals[OWNER] + 1
    assert get(history, OWNER, f"/api/audit/{bad}").status_code == 200
    assert get(history, ADMIN, "/api/audit").json()["total"] == totals[ADMIN]
    assert get(history, ADMIN, f"/api/audit/{bad}").status_code == 404


def test_storing_a_review_target_with_an_unresolvable_path_reports_its_media_unavailable(
    review_app,
):
    item_id = review_app.jellyfin_item(guid("store-target"), LIBRARY_A)
    review_app.items[item_id] = review_app.items[item_id].model_copy(
        update={"path": "\\media\\store-target.mkv"}
    )
    _tolerant(review_app.client)

    response = review_app.send(
        "POST",
        f"/api/library-items/{item_id}/reviews",
        review_app.accounts.unsafe_cookie(OWNER),
        {"name": "unresolvable"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "media_unavailable"


def test_a_mapping_refusal_keeps_its_input_error(start):
    app = start()
    owner = app.accounts.unsafe_cookie(OWNER)
    for path, body in [
        ("/api/settings/path-mappings", {"remote_path": "\\media", "local_path": "/srv"}),
        ("/api/settings/path-mappings/resolve", {"remote_path": "https://stream.test/x.mkv"}),
    ]:
        response = app.send("POST", path, owner, body)
        assert response.status_code == 400, (path, response.text)
        assert response.json()["detail"] == "Remote path must be an absolute POSIX or Windows path"


@pytest.mark.parametrize(
    ("host", "server_name"),
    [(ntpath, "{here}/a\\b.mkv"), (os.path, "https://stream.test/x.mkv")],
    ids=["windows-unnameable", "posix-url"],
)
def test_scan_evidence_lets_no_unresolvable_server_file_vouch_for_a_host_file(
    linked, clock, monkeypatch, tmp_path, host, server_name
):
    """The server path's raw spelling names an existing host file outside every library."""

    monkeypatch.chdir(tmp_path)
    server_path = server_name.format(here=tmp_path)
    spelled = Path(os.path.realpath(server_path))
    spelled.parent.mkdir(parents=True, exist_ok=True)
    spelled.touch()
    with linked.factory() as db:
        create_path_mapping(db, PathMappingCreate(remote_path="/jellyfin", local_path=str(MEDIA)))
    monkeypatch.setattr(repository_paths, "_HOST_PATHS", host)
    item = media_access_support.JELLYFIN_ITEMS[FAMILY_FILM]

    def decided(path):
        cache = EvidenceCache(adapters=lambda _db: ScriptedAdapter(clock))
        with linked.factory() as db:
            account = linked.access(USER).principal.account
            target = media_access_support.path_target(path)

            def local(server_path):
                return repository_paths.resolve_local_path(db, server_path).local_path

            with cache.evidence(db, account, local, clock()) as evidence:
                return decide_media(
                    load_subject(db, account),
                    [target],
                    user=evidence.user,
                    catalog=evidence.catalog,
                    libraries=evidence.libraries,
                    items=evidence.items,
                    now=clock(),
                )[target.key]

    assert isinstance(decided(resolve(item.path)), MediaAllowed)
    monkeypatch.setitem(
        media_access_support.JELLYFIN_ITEMS,
        FAMILY_FILM,
        item.model_copy(update={"path": server_path, "versions": []}),
    )
    assert decided(str(spelled)) == MediaDenied(MediaDenial.UNMATCHED)
