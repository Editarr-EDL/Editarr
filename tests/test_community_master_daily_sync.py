"""The daily master-set sync: once per day per account, and its notification kinds."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from scanner.db import CommunityMasterLinkRow, CommunitySyncNotificationRow, MediaVersionRow
from scanner.integrations.community.master_sync import MasterSync
from scanner.integrations.community.masters import MasterSets
from scanner.integrations.community.transport import CommunityFailure
from tests.community_master_service_support import (
    FakeService,
    connect,
    error,
    master_response,
    master_route,
    served_filter,
)
from tests.community_master_support import OWNER, T0, VERSION_ID


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def linked(tmp_path):
    """Three links: v_1 (changes), v_2 (merged) and v_3 (unavailable)."""
    service = FakeService()
    factory, client = connect(tmp_path, service)
    with factory() as db:
        db.add_all([MediaVersionRow(id=f"mv_{n}", title_id="title") for n in (2, 3)])
        db.commit()
    masters = MasterSets(client, OWNER)
    links = {}
    for version, local in (("v_1", VERSION_ID), ("v_2", "mv_2"), ("v_3", "mv_3")):
        master_route(service, version, {None: master_response(version, 1, served_filter("f_a"))})
        links[version] = asyncio.run(
            masters.download(media_version_id=local, community_version_id=version, tier="1")
        ).id
    master_route(
        service,
        "v_1",
        {1: master_response("v_1", 2, served_filter("f_a"), served_filter("f_b", 50_000, 60_000))},
    )
    service.on(
        "POST",
        "/v1/sync/revisions",
        lambda _request: {
            "version": 1,
            "changed": [
                {"versionId": "v_1", "status": "changed", "revision": 2},
                {"versionId": "v_2", "status": "merged", "revision": None, "mergedInto": "v_1"},
                {"versionId": "v_3", "status": "unavailable", "revision": None},
            ],
            "nextRunAllowedAt": 1,
        },
    )
    clock = Clock()
    yield service, factory, MasterSync(client, OWNER, clock=clock), clock, links
    client.db.close()


def test_sync_runs_once_per_day_and_records_each_notification_kind(linked):
    service, factory, sync, clock, links = linked
    outcome = asyncio.run(sync.run())
    assert outcome.ran and outcome.status.state == "idle"
    (sent,) = service.sent("POST", "/v1/sync/revisions")
    assert json.loads(sent.content)["items"] == [
        {"versionId": version, "revision": 1} for version in ("v_1", "v_2", "v_3")
    ]
    with factory() as db:
        notes = {
            (note.link_id, note.kind, note.from_revision, note.to_revision)
            for note in db.scalars(select(CommunitySyncNotificationRow))
        }
        assert notes == {
            (links["v_1"], "changed", 1, 2),
            (links["v_2"], "merged", 1, None),
            (links["v_3"], "unavailable", 1, None),
        }
        states = {
            link.community_version_id: link for link in db.scalars(select(CommunityMasterLinkRow))
        }
        assert states["v_1"].latest_revision == 2 and states["v_1"].applied_revision == 1
        assert (states["v_2"].link_state, states["v_2"].merged_into) == ("merged", "v_1")
        assert states["v_3"].link_state == "unavailable"
    assert outcome.status.unread_count == 3

    clock.now += timedelta(hours=23)
    assert asyncio.run(sync.run()).ran is False
    assert len(service.sent("POST", "/v1/sync/revisions")) == 1
    clock.now += timedelta(hours=2)
    service.on(
        "POST",
        "/v1/sync/revisions",
        lambda _r: {"version": 1, "changed": [], "nextRunAllowedAt": 1},
    )
    assert asyncio.run(sync.run()).ran is True
    (later,) = service.sent("POST", "/v1/sync/revisions")[1:]
    assert json.loads(later.content)["items"] == [{"versionId": "v_1", "revision": 2}]


def test_a_rate_limited_run_fails_and_stays_due(linked):
    service, _factory, sync, _clock, _links = linked
    service.on(
        "POST", "/v1/sync/revisions", lambda _r: error(429, "sync_rate_limited", retry_after=600)
    )
    with pytest.raises(CommunityFailure, match="sync_rate_limited"):
        asyncio.run(sync.run())
    status = sync.status()
    assert (status.state, status.last_error, status.due) == ("failed", "sync_rate_limited", True)


def test_a_version_merged_during_download_is_marked_merged(linked):
    service, factory, sync, _clock, links = linked
    master_route(service, "v_1", {1: error(409, "version_merged", mergedInto="v_7")})
    assert asyncio.run(sync.run()).ran
    with factory() as db:
        link = db.get(CommunityMasterLinkRow, links["v_1"])
        assert (link.link_state, link.merged_into) == ("merged", "v_7")
        kinds = db.scalars(
            select(CommunitySyncNotificationRow.kind).where(
                CommunitySyncNotificationRow.link_id == links["v_1"]
            )
        ).all()
        assert kinds == ["merged"]
