"""Daily sync with more changed versions than `GET …/master` allows per hour (local API §8)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from scanner.db import CommunityMasterLinkRow, CommunitySyncNotificationRow, MediaVersionRow
from scanner.integrations.community.master_sync import MasterSync
from scanner.integrations.community.masters import MasterSets
from tests.community_master_service_support import (
    FakeService,
    connect,
    error,
    found_download,
    master_response,
    served_filter,
)
from tests.community_master_support import OWNER, T0

VERSIONS = [f"v_{n:02d}" for n in range(62)]
HOURLY = 60


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class LimitedMasters:
    """Serve revision 1, or revision 2 to `knownRevision=1`, until `HOURLY` checks."""

    def __init__(self):
        self.checks = 0
        self.limited = False

    def __call__(self, request):
        version = request.url.path.split("/")[3]
        if request.url.params.get("knownRevision") is None:
            return master_response(version, 1, served_filter("f_a"))
        self.checks += 1
        if self.limited and self.checks > HOURLY:
            return error(429, "rate_limited", retry_after=600)
        return master_response(
            version, 2, served_filter("f_a"), served_filter("f_b", 50_000, 60_000)
        )


@pytest.fixture
def many(tmp_path):
    service = FakeService()
    factory, client = connect(tmp_path, service)
    with factory() as db:
        db.add_all([MediaVersionRow(id=f"mv_{version}", title_id="title") for version in VERSIONS])
        db.commit()
    masters_route = LimitedMasters()
    for version in VERSIONS:
        service.on("GET", f"/v1/versions/{version}/master", masters_route)
    masters = MasterSets(client, OWNER)
    for version in VERSIONS:
        found_download(masters, version, tier="1", local=f"mv_{version}")
    masters_route.limited = True
    service.on(
        "POST",
        "/v1/sync/revisions",
        lambda _r: {
            "version": 1,
            "changed": [
                {"versionId": version, "status": "changed", "revision": 2} for version in VERSIONS
            ],
            "nextRunAllowedAt": 1,
        },
    )
    clock = Clock()
    yield service, factory, MasterSync(client, OWNER, clock=clock), clock, masters_route
    client.db.close()


def _state(factory):
    with factory() as db:
        links = list(db.scalars(select(CommunityMasterLinkRow)))
        notes = db.scalar(select(func.count()).select_from(CommunitySyncNotificationRow))
        return (
            sum(link.latest_revision == 2 for link in links),
            sum(link.pending_revision == 2 for link in links),
            notes,
        )


def test_rate_limited_downloads_stay_pending_and_drain_without_a_new_run(many):
    service, factory, sync, clock, masters_route = many
    outcome = asyncio.run(sync.run())
    assert outcome.ran and outcome.status.state == "idle" and outcome.status.last_error is None
    assert outcome.status.pending_downloads == len(VERSIONS) - HOURLY
    # Each `changed` notification is recorded once its snapshot is downloaded.
    assert _state(factory) == (HOURLY, len(VERSIONS) - HOURLY, HOURLY)

    # Before `Retry-After` passes a page load does not reach the service.
    requests = len(service.requests)
    assert asyncio.run(sync.run()).ran is False
    assert len(service.requests) == requests

    clock.now += timedelta(seconds=601)
    masters_route.checks = 0  # the service's next hourly window
    outcome = asyncio.run(sync.run())
    assert outcome.ran is False and outcome.status.pending_downloads == 0
    assert _state(factory) == (len(VERSIONS), 0, len(VERSIONS))
    assert len(service.sent("POST", "/v1/sync/revisions")) == 1
