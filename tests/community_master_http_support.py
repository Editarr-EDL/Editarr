"""Master-set routes behind the real access middleware, against a fake signed service.

Three accounts enroll their own contributors. The fake service verifies every
request signature, signs master snapshots and receipts for the requesting
contributor, and answers match, sync, vote and report calls. Jellyfin target
resolution and key measurement are replaced with one seeded local version, so the
route, access and persistence boundaries are real.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx
import pytest

import scanner.api  # noqa: F401 - load the application before its route modules
from scanner.db import MediaItemRow, MediaTitleRow, MediaVersionRow, ScanRow
from scanner.http import (
    community_activity_access,
    community_api,
    community_identity_api,
    community_master_targets,
    community_masters_api,
    review_targets,
)
from scanner.http.community_master_alignment_api import previews
from scanner.integrations.community.find_store import finds
from scanner.integrations.community.find_targets import CapturedFind
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.modeling.community_normalization import SEEDED_ALIASES
from scanner.persistence.repository.repository_community import identity_connection
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.persistence.rows.db_media_identity import MediaVersionLocatorRow
from tests.community_identity_users_support import AUDIENCE, SERVICE, MockService
from tests.community_master_service_support import (
    MASTER_FIXTURE,
    master_response,
    respond,
    served_filter,
)
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_community_activity_access import activities as activities
from tests.test_community_identity_users import _enroll
from tests.test_community_master_find import MD5, keys
from tests.test_community_master_service import MATCH
from tests.users_api_http_support import UsersApp

VERSION_ID = "mv_http"
API = "/api/community"
_MASTER = re.compile(r"^/v1/versions/([A-Za-z0-9_.-]+)/master$")
_VOTE = re.compile(r"^/v1/filters/([A-Za-z0-9_.-]+)/votes$")


def error(status: int, code: str, **details) -> httpx.Response:
    return respond(status, {"error": {"code": code, **details}})


class MasterService(MockService):
    """The signed master-set endpoints for every enrolled test contributor."""

    def __init__(self):
        super().__init__()
        self.published: dict[str, tuple[int, tuple[dict, ...]]] = {}
        self.gone: dict[str, httpx.Response] = {}
        self.match_results: list[dict] = []
        self.sync_changes: list[dict] = []
        self.vote_failures: list[httpx.Response] = []
        self.votes: list[dict] = []
        self.reports: list[dict] = []

    def publish(self, version: str, revision: int, *filters: dict) -> None:
        self.published[version] = (revision, filters)

    def _master(self, version: str, contributor: str, known: str | None):
        if version in self.gone:
            return self.gone[version]
        revision, filters = self.published[version]
        if known is not None and int(known) >= revision:
            return respond(
                200, {"version": 1, "changed": False, "versionId": version, "revision": int(known)}
            )
        document = master_response(
            version, revision, *filters, contributor=contributor, service=SERVICE
        )
        return respond(200, document)

    def _vote(self, request, filter_id: str):
        if self.vote_failures:
            return self.vote_failures.pop(0)
        body = json.loads(request.content)
        self.votes.append({"filterId": filter_id, **body})
        version = next(
            name
            for name, (_revision, filters) in self.published.items()
            if any(item["filterId"] == filter_id for item in filters)
        )
        result = {
            "version": 1,
            "operationId": request.headers["idempotency-key"],
            "filterId": filter_id,
            "versionId": version,
            "vote": body["vote"],
            "revision": self.published[version][0],
        }
        return respond(200, {"version": 1, "state": "completed", "result": result})

    def _sync(self, request):
        sent = {item["versionId"] for item in json.loads(request.content)["items"]}
        changed = [change for change in self.sync_changes if change["versionId"] in sent]
        return respond(200, {"version": 1, "changed": changed, "nextRunAllowedAt": 0})

    def _report(self, request):
        self.reports.append(json.loads(request.content))
        result = {
            "version": 1,
            "operationId": request.headers["idempotency-key"],
            "reportId": "r_1",
            "state": "open",
        }
        return respond(200, {"version": 1, "state": "completed", "result": result})

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/receipt-keys":
            self._signer(request)
            keys_ = MASTER_FIXTURE["receiptKeys"]
            return respond(200, {"version": 1, "service": SERVICE, "keys": keys_})
        if path == "/v1/aliases":
            self._signer(request)
            aliases = [
                {"aliasId": a.alias_id, "label": a.label, "spellings": list(a.spellings)}
                for a in SEEDED_ALIASES
            ]
            return respond(200, {"version": 1, "aliases": aliases})
        if path == "/v1/match":
            self._signer(request)
            return respond(200, {"version": 1, "results": self.match_results})
        if path == "/v1/sync/revisions":
            self._signer(request)
            return self._sync(request)
        if path == "/v1/reports":
            self._signer(request)
            return self._report(request)
        if found := _MASTER.match(path):
            contributor = self.contributors[self._signer(request)]
            return self._master(found[1], contributor, request.url.params.get("knownRevision"))
        if found := _VOTE.match(path):
            self._signer(request)
            return self._vote(request, found[1])
        return await super().__call__(request)


def match(version: str, tier: str = "2") -> dict:
    """A `/v1/match` result consistent with the seeded MD5 and TMDB keys."""
    if tier == "2":
        return {**MATCH, "versionId": version, "revision": 1}
    return {
        **MATCH,
        "versionId": version,
        "revision": 1,
        "tier": "4",
        "matchPercent": 30,
        "matchedKeys": ["tmdb"],
        "runtimeAgreement": "same_cut",
        "runtimeMs": 8_160_000,
    }


@dataclass
class Masters:
    app: UsersApp
    service: MasterService
    item_id: str
    path: str
    library_id: str
    scans: object
    contributors: dict = field(default_factory=dict)

    @property
    def target(self) -> dict:
        return {"source": "jellyfin", "item_id": self.item_id, "media_source_id": None}

    def send(self, method, path, user, body=None):
        return self.app.send(method, API + path, user, body)

    def ok(self, method, path, user, body=None, status=200):
        response = self.send(method, path, user, body)
        assert response.status_code == status, response.text
        return response.json() if response.content else None

    def find(self, user, version="v_1", tier="2") -> dict:
        self.service.match_results = [match(version, tier)]
        return self.ok("POST", "/find", user, {"target": self.target, "label": None})

    def apply(self, user, version="v_1", tier="2", *, replace=False, find_key=None):
        """Find then download `version`; returns the response."""
        key = find_key or self.find(user, version, tier)["findKey"]
        body = {
            "target": self.target,
            "find_key": key,
            "community_version_id": version,
            "confirmed": True,
        }
        if replace:
            body["replace"] = True
        return self.send("POST", "/masters", user, body)

    def hide_media(self, user) -> None:
        """Limit the account to another library, so the seeded item is out of reach."""
        from tests.scan_access_http import LIBRARY_B

        self.scans.limit_to_library(user, LIBRARY_B)

    def link(self, user, version="v_1", tier="2", *filters) -> dict:
        self.service.publish(version, 1, *(filters or (served_filter("f_a"),)))
        response = self.apply(user, version, tier)
        assert response.status_code == 200, response.text
        return response.json()["link"]


@pytest.fixture
def masters(activities, monkeypatch):
    configuration = activities.accounts.configuration.model_copy(
        update={"community_service_url": SERVICE, "community_service_audience": AUDIENCE}
    )
    service = MasterService()
    homes = {
        user: _enroll(activities.accounts, service, user, character * 64)
        for user, character in ((USER, "a"), (ADMIN, "b"), (PEER_USER, "c"))
    }
    for module in (community_api, community_identity_api, community_activity_access):
        monkeypatch.setattr(module, "settings", configuration)
    monkeypatch.setattr(
        community_identity_api,
        "service_transport",
        lambda: CommunityTransport(
            ServiceEndpoint(SERVICE, AUDIENCE), adapter=httpx.MockTransport(service)
        ),
    )
    client = serve_access_fixture(monkeypatch, activities.accounts)
    client.app.include_router(community_api.router)
    scan, _ = activities.seed_scan(USER, "masters", status="complete")
    with activities.accounts.factory() as db:
        row = db.get(ScanRow, scan)
        item = db.get(MediaItemRow, row.media_item_id)
        db.add(MediaTitleRow(id="title-http", entity_type="movie"))
        db.flush()
        db.add(MediaVersionRow(id=VERSION_ID, title_id="title-http"))
        db.flush()
        db.add(
            MediaVersionLocatorRow(
                version_id=VERSION_ID,
                media_item_id=item.id,
                media_source_id="",
                source_path=item.path,
                resolved_path=row.resolved_local_path,
            )
        )
        db.commit()
        media = ScanMedia(
            item.server_id, item.jellyfin_id, item.library_id, row.resolved_local_path
        )
        state = Masters(
            UsersApp(client, activities.accounts),
            service,
            item.jellyfin_id,
            row.resolved_local_path,
            item.library_id,
            activities,
        )
        for user, home in homes.items():
            connection = identity_connection(db, home.credential_ref, SERVICE)
            state.contributors[user] = connection.contributor_id

    monkeypatch.setattr(
        review_targets, "capture_target", lambda _caller, _target: SimpleNamespace(media=media)
    )
    monkeypatch.setattr(
        community_masters_api,
        "capture_find_keys",
        lambda _db, _target: CapturedFind(media.resolved_path, (), keys(MD5), None, "", ()),
    )
    monkeypatch.setattr(
        community_master_targets, "local_version", lambda _db, _target, _sampled: VERSION_ID
    )
    yield state
    client.close()
    finds.records.clear()
    previews.records.clear()
