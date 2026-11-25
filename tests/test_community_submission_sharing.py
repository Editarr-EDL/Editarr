"""Sharing as v3 submissions: source capture, previews with estimates, exact-byte
approval and the `submission` outbox (local API §7, contract §7.2)."""

import asyncio
import base64
import gzip
import hashlib
import json

import pytest

from scanner.db import (
    ArchivedSource,
    CommunityMasterLinkRow,
    CommunityOperationRow,
    CommunityPublicationRow,
    MediaItemRow,
    MediaVersionRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.formats.community.codec import ArtifactError, ResourceBounds
from scanner.integrations.community import master_service, submissions
from scanner.integrations.community.donation_sources import SourceRef, capture_source
from scanner.integrations.community.masters import MasterSets
from scanner.integrations.community.sharing import PreviewStore, SharingService
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_match_keys import HashKey
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
from scanner.persistence.archive import archived_reads
from scanner.persistence.repository.repository_community import identity_connection
from tests.community_master_service_support import (
    FakeService,
    connect,
    error,
    found_download,
    master_response,
    master_route,
    served_filter,
)
from tests.community_master_support import OWNER, SERVICE

BOUNDS = ResourceBounds(65536, 524288, 100, 4096, 24)
MOVIE = MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "603", "user_confirmed"),))
THEATRICAL = {"choice": "alias", "alias_id": "theatrical"}
KEY = HashKey("md5_quick", "a" * 32, 1234)


def accept_submission(request):
    body = json.loads(request.content)
    operation = request.headers["idempotency-key"]
    assert operation == "submit-" + body["submissionId"]
    return {
        "version": 1,
        "state": "completed",
        "result": {
            "version": 1,
            "operationId": operation,
            "submissionId": body["submissionId"],
            "submissionDigest": hashlib.sha256(base64.b64decode(body["artifact"])).hexdigest(),
            "workId": "w_77",
            "versionId": "v_1",
            "versionStatus": "confirmed",
            "disposition": "attached",
            "tier": "2",
            "revision": 2,
            "supersedes": None,
            "outcome": {
                "newFilters": 1,
                "supports": 1,
                "disputes": 1,
                "coverageNegatives": 0,
                "ignored": 0,
                "unassigned": 0,
            },
            "aliasSuggestion": None,
        },
    }


ALIASES = [
    {"aliasId": "theatrical", "label": "Theatrical", "spellings": ["", "theatrical"]},
    {"aliasId": "directors_cut", "label": "Director's Cut", "spellings": ["directors cut"]},
    {"aliasId": "extended", "label": "Extended Edition", "spellings": ["extended"]},
    {"aliasId": "imax", "label": "IMAX", "spellings": ["imax"]},
    {"aliasId": "unrated", "label": "Unrated", "spellings": ["unrated"]},
]


@pytest.fixture(autouse=True)
def _fresh_alias_cache(monkeypatch):
    monkeypatch.setattr(master_service, "_aliases", {})


@pytest.fixture
def sharing(tmp_path, monkeypatch):
    """A connected sharing service and a settled generated scan with two kept cues."""
    service = FakeService()
    factory, client = connect(tmp_path, service)
    # The scan's file is not on disk here; file keys are covered by their own capture.
    monkeypatch.setattr(submissions, "_media_keys", lambda *_args: (KEY,))
    with factory() as db:
        scan = db.get(ScanRow, "scan")
        scan.duration_seconds = 600.0
        scan.release_version = "1.4.0"
        db.add(
            ScanFindingRow(
                scan_id="scan",
                category="language",
                subcategory="swearing",
                severity="low",
                start_seconds=300.0,
                end_seconds=301.0,
                source="audio",
                details="PRIVATE FULL TRANSCRIPT",
            )
        )
        finding = db.query(ScanFindingRow).filter_by(category="violence").one()
        finding.severity = "medium"
        db.commit()
    service.on("POST", "/v1/submissions", accept_submission)
    yield (
        service,
        factory,
        SharingService(
            client, bounds=BOUNDS, source_limit=4, request_bytes=200_000, store=PreviewStore()
        ),
    )
    client.db.close()


def prepare(sharing, **options):
    return sharing.prepare(
        SourceRef("scan", "scan"),
        owner="session-a",
        name="Public name",
        media=MOVIE,
        version_choice=options.pop("version_choice", THEATRICAL),
        **options,
    )


def approve(sharing, preview):
    return asyncio.run(sharing.approve(preview["previewId"], owner="session-a", confirmed=True))


def test_source_capture_is_immutable_and_never_reads_archived_work(sharing):
    _service, factory, service = sharing
    reference = SourceRef("scan", "scan")
    captured = capture_source(service.db.get_bind(), reference, bounds=BOUNDS, source_limit=4)
    with factory() as db, archived_reads(db):
        db.get(MediaItemRow, "item").archived_source = ArchivedSource.RADARR
        db.commit()
    with pytest.raises(ArtifactError, match="Donation source is unavailable"):
        capture_source(service.db.get_bind(), reference, bounds=BOUNDS, source_limit=4)
    assert captured.data()["root"]["id"] == "scan"


def test_preview_is_local_and_reports_estimates_without_enabling_sharing(sharing):
    fake, _factory, service = sharing
    preview = prepare(service)
    artifact = preview["preview"]["artifact"]
    assert artifact["schemaVersion"] == "community-artifact-3"
    assert artifact["work"]["tmdb"] == "603" and artifact["work"]["title"] == "M"
    assert artifact["version"] == {"choice": "alias", "aliasId": "theatrical"}
    assert artifact["media"]["matchKeys"] == [
        {"type": "md5_quick", "value": "a" * 32, "sizeBytes": 1234}
    ]
    assert preview["estimates"] == {
        "approximate": True,
        "newFilters": 2,
        "confirmations": 0,
        "adjustments": 0,
        "disputes": 0,
    }
    assert preview["versionChoice"]["label"] == "Theatrical" and preview["entryCount"] == 2
    assert "PRIVATE FULL TRANSCRIPT" not in json.dumps(preview)
    assert {request.url.path for request in fake.requests} <= {"/v1/aliases"}
    assert service.db.query(CommunityOperationRow).count() == 0
    assert (
        identity_connection(service.db, service.client.home.credential_ref, SERVICE).sharing_enabled
        is False
    )


def test_preview_projects_against_the_accounts_linked_master_set(sharing):
    fake, _factory, service = sharing
    first = master_response(
        "v_1",
        1,
        served_filter("f_hit", 100_100, 101_200),
        served_filter("f_no", 400_000, 401_000),
    )
    second = master_response("v_1", 2, served_filter("f_hit", 100_000, 101_200))
    master_route(fake, "v_1", {None: first, 1: second})
    masters = MasterSets(service.client, OWNER)
    link = found_download(masters, "v_1")
    preview = prepare(service)
    entries = preview["preview"]["artifact"]["entries"]
    assert {(entry["kind"], entry["proposedFilterId"]) for entry in entries} == {
        ("support", "f_hit"),
        ("support", None),
    }
    assert preview["preview"]["artifact"]["alignmentHint"]["versionId"] == "v_1"
    assert preview["estimates"]["confirmations"] == 1 and preview["estimates"]["disputes"] == 0
    asyncio.run(masters.check(link.id))
    with pytest.raises(CommunityFailure, match="preview_stale"):
        approve(service, preview)


def test_alias_choices_follow_the_services_approved_list(sharing):
    fake, _factory, service = sharing
    with pytest.raises(CommunityFailure, match="unknown_alias"):
        prepare(service, version_choice={"choice": "alias", "alias_id": "imax"})
    fake.on("GET", "/v1/aliases", lambda _r: {"version": 1, "aliases": ALIASES})
    master_service._aliases.clear()
    preview = prepare(service, version_choice={"choice": "alias", "alias_id": "imax"})
    assert preview["versionChoice"] == {"choice": "alias", "aliasId": "imax", "label": "IMAX"}
    fake.on("GET", "/v1/aliases", lambda _r: error(503, "service_unavailable"))
    assert prepare(service, version_choice={"choice": "alias", "alias_id": "imax"})
    assert len(fake.sent("GET", "/v1/aliases")) == 2


def test_a_link_must_follow_the_sources_version_and_service(sharing):
    fake, factory, service = sharing
    master_route(fake, "v_1", {None: master_response("v_1", 1, served_filter("f_hit"))})
    link = found_download(MasterSets(service.client, OWNER), "v_1")
    with factory() as db:
        db.add(MediaVersionRow(id="mv_other", title_id="title"))
        db.commit()
    other = found_download(MasterSets(service.client, OWNER), "v_1", local="mv_other")
    with pytest.raises(CommunityFailure, match="link_unavailable"):
        prepare(service, link_id=other.id)
    with factory() as db:
        db.get(CommunityMasterLinkRow, link.id).service_url = "https://other.example"
        db.commit()
    with pytest.raises(CommunityFailure, match="link_unavailable"):
        prepare(service, link_id=link.id)


def test_the_shared_title_and_year_come_from_jellyfin_like_find(sharing, monkeypatch):
    from scanner.integrations.community import find_targets

    _fake, _factory, service = sharing
    monkeypatch.setattr(find_targets, "get_jellyfin_credentials", lambda _db: ("s" * 32, "key"))

    class Jellyfin:
        def __init__(self, *_credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item(self, item_id):
            assert item_id == "j" * 32
            return type("Item", (), {"name": "The Matrix", "year": 1999})()

    monkeypatch.setattr(find_targets, "JellyfinClient", Jellyfin)
    work = prepare(service)["preview"]["artifact"]["work"]
    assert (work["title"], work["year"]) == ("The Matrix", 1999)


def test_version_choice_and_work_identity_are_required(sharing):
    _fake, _factory, service = sharing
    with pytest.raises(CommunityFailure, match="version_choice_required"):
        prepare(service, version_choice=None)
    with pytest.raises(CommunityFailure, match="unknown_alias"):
        prepare(service, version_choice={"choice": "alias", "alias_id": "imax"})
    with pytest.raises(CommunityFailure, match="work_identity_required"):
        service.prepare(
            SourceRef("scan", "scan"),
            owner="session-a",
            name="Public name",
            media=MediaIdentity("movie", ()),
            version_choice=THEATRICAL,
        )


def test_approval_queues_a_submission_and_only_retry_sends_the_exact_bytes(sharing):
    fake, factory, service = sharing
    preview = prepare(service)
    job = approve(service, preview)
    assert job["state"] == "pending" and job["operationId"].startswith("submit-sub_")
    assert (
        identity_connection(service.db, service.client.home.credential_ref, SERVICE).sharing_enabled
        is True
    )
    service.db.rollback()
    outbox = service.outbox
    assert asyncio.run(outbox.run(job["operationId"]))["state"] == "pending"
    assert fake.sent("POST", "/v1/submissions") == []
    published = asyncio.run(outbox.run(job["operationId"], resend=True))
    assert published["state"] == "published" and published["result"]["revision"] == 2
    (sent,) = fake.sent("POST", "/v1/submissions")
    with factory() as db:
        operation = db.get(CommunityOperationRow, job["operationId"])
        payload = db.get(CommunityPublicationRow, job["operationId"])
        assert operation.kind == "submission" and operation.state == "acknowledged"
        body = json.loads(sent.content)
        assert base64.b64decode(body["artifact"]) == payload.artifact_bytes
        artifact = json.loads(gzip.decompress(payload.artifact_bytes))
        assert artifact["submissionId"] == operation.revision_id == body["submissionId"]
    asyncio.run(outbox.run(job["operationId"], resend=True))
    assert len(fake.sent("POST", "/v1/submissions")) == 1


@pytest.mark.parametrize(
    ("status", "code", "state"),
    [(422, "unknown_alias", "rejected"), (503, "service_unavailable", "paused")],
)
def test_submission_failures_keep_the_exact_evidence(sharing, status, code, state):
    fake, factory, service = sharing
    job = approve(service, prepare(service))
    fake.on("POST", "/v1/submissions", lambda _r: error(status, code))
    result = asyncio.run(service.outbox.run(job["operationId"], resend=True))
    assert (result["state"], result["errorCode"]) == (state, code)
    with factory() as db:
        assert db.get(CommunityPublicationRow, job["operationId"]).artifact_bytes


def test_cancel_before_send_is_local(sharing):
    fake, _factory, service = sharing
    job = approve(service, prepare(service))
    assert service.outbox.cancel(job["operationId"])["state"] == "cancelled"
    asyncio.run(service.outbox.run(job["operationId"], resend=True))
    assert fake.sent("POST", "/v1/submissions") == []


def test_changed_source_invalidates_the_preview_before_any_network(sharing):
    fake, factory, service = sharing
    preview = prepare(service)
    with factory() as db:
        db.get(ScanRow, "scan").name = "Source edited"
        db.commit()
    with pytest.raises(CommunityFailure, match="preview_stale"):
        approve(service, preview)
    assert not fake.sent("POST", "/v1/submissions")
    assert service.db.query(CommunityOperationRow).count() == 0


def test_preview_store_expiry_scope_and_discard(sharing):
    _fake, _factory, service = sharing
    preview = prepare(service)
    with pytest.raises(CommunityFailure, match="preview_unavailable"):
        service.store.get(preview["previewId"], "other-session", service.scope)
    service.store.discard(preview["previewId"], "session-a", service.scope)
    with pytest.raises(CommunityFailure, match="preview_expired"):
        service.store.get(preview["previewId"], "session-a", service.scope)


@pytest.mark.parametrize(
    "provider,entity,url,identifier",
    [
        ("tmdb", "movie", "https://www.themoviedb.org/movie/123-title", "123"),
        ("tvdb", "movie", "https://www.thetvdb.com/dereferrer/movie/123", "123"),
        ("tvdb", "episode", "https://thetvdb.com/series/psych/episodes/307497", "307497"),
    ],
)
def test_public_reference_urls_and_ids_agree(provider, entity, url, identifier):
    from scanner.http.community_sharing_api import PublicReference

    first = PublicReference(provider=provider, entity_type=entity, reference=url).identity()
    second = PublicReference(provider=provider, entity_type=entity, reference=identifier).identity()
    assert first == second
