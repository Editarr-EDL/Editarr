"""Download, apply and update checks for master sets, and the local verification hook."""

import asyncio

import pytest
from sqlalchemy import select

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityMasterUpdateRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.integrations.community.masters import MasterSets
from scanner.integrations.community.transport import CommunityFailure
from scanner.persistence.repository.community_master_filters import (
    refresh_scan_verification,
    set_filter_decision,
)
from scanner.persistence.repository.community_master_links import approve_alignment
from scanner.persistence.repository.repository_queue import update_scan_status
from tests.community_master_service_support import (
    FakeService,
    connect,
    error,
    master_response,
    master_route,
    served_filter,
)
from tests.community_master_support import OWNER, VERSION_ID

OVERLAPS_FINDING = served_filter("f_hit", 100_000, 101_000)
ELSEWHERE = served_filter("f_far", 500_000, 501_000)


@pytest.fixture
def setup(tmp_path):
    service = FakeService()
    factory, client = connect(tmp_path, service)
    yield service, factory, MasterSets(client, OWNER)
    client.db.close()


def download(masters, tier="2", replace=False, version="v_1"):
    return asyncio.run(
        masters.download(
            media_version_id=VERSION_ID,
            community_version_id=version,
            tier=tier,
            match_percent=80,
            replace=replace,
        )
    )


def rows(factory, link_id):
    with factory() as db:
        return {
            row.filter_id: (row.state, row.locally_verified)
            for row in db.scalars(
                select(CommunityMasterFilterRow).where(CommunityMasterFilterRow.link_id == link_id)
            )
        }


def test_tier_two_applies_directly_and_marks_overlaps_locally_verified(setup):
    service, factory, masters = setup
    master_route(service, "v_1", {None: master_response("v_1", 3, OVERLAPS_FINDING, ELSEWHERE)})
    link = download(masters)
    with factory() as db:
        stored = db.get(CommunityMasterLinkRow, link.id)
        assert (stored.alignment_state, stored.tier, stored.latest_revision) == (
            "not_required",
            "2",
            3,
        )
        assert stored.contributor_id == "c_7f3a" and stored.label == "Theatrical"
    assert rows(factory, link.id) == {"f_hit": ("pending", True), "f_far": ("pending", False)}


def test_fuzzy_tiers_require_alignment_before_local_verification(setup):
    service, factory, masters = setup
    master_route(service, "v_1", {None: master_response("v_1", 3, OVERLAPS_FINDING)})
    link = download(masters, tier="4")
    with factory() as db:
        assert db.get(CommunityMasterLinkRow, link.id).alignment_state == "required"
    assert rows(factory, link.id) == {"f_hit": ("pending", False)}
    with factory() as db:
        approve_alignment(db, OWNER, link.id, {"offsetMs": 0, "scale": 1.0, "anchors": 3}, 3)
        refresh_scan_verification(db, "scan")
        db.commit()
    assert rows(factory, link.id) == {"f_hit": ("pending", True)}


def test_another_version_needs_replace_and_merged_versions_report_their_target(setup):
    service, _factory, masters = setup
    master_route(service, "v_1", {None: master_response("v_1", 1, ELSEWHERE)})
    master_route(service, "v_2", {None: master_response("v_2", 1, ELSEWHERE)})
    first = download(masters)
    from scanner.persistence.repository.community_master_links import CommunityMasterError

    with pytest.raises(CommunityMasterError, match="replace_required"):
        download(masters, version="v_2")
    assert download(masters, version="v_2", replace=True).id != first.id
    master_route(service, "v_3", {None: error(409, "version_merged", mergedInto="v_2")})
    with pytest.raises(CommunityFailure) as raised:
        download(masters, version="v_3", replace=True)
    assert raised.value.details["mergedInto"] == "v_2"


def test_check_stores_a_newer_revision_for_review_without_applying_it(setup):
    service, factory, masters = setup
    unchanged = {"version": 1, "changed": False, "versionId": "v_1", "revision": 1}
    master_route(
        service,
        "v_1",
        {
            None: master_response("v_1", 1, ELSEWHERE),
            1: master_response("v_1", 2, ELSEWHERE, served_filter("f_new", 700_000, 701_000)),
            2: {**unchanged, "revision": 2},
        },
    )
    link = download(masters)
    result = asyncio.run(masters.check(link.id))
    assert result["changed"] is True and result["latestRevision"] == 2
    assert result["pendingUpdate"]["new"] == 1
    with factory() as db:
        assert db.get(CommunityMasterLinkRow, link.id).applied_revision == 1
        assert db.scalars(select(CommunityMasterUpdateRow.filter_id)).all() == ["f_new"]
    assert asyncio.run(masters.check(link.id))["changed"] is False


@pytest.mark.parametrize(
    ("response", "state"),
    [
        (error(409, "version_merged", mergedInto="v_9"), "merged"),
        (error(410, "version_unavailable"), "unavailable"),
    ],
)
def test_check_marks_gone_versions(setup, response, state):
    service, factory, masters = setup
    master_route(service, "v_1", {None: master_response("v_1", 1, ELSEWHERE), 1: response})
    link = download(masters)
    with pytest.raises(CommunityFailure):
        asyncio.run(masters.check(link.id))
    with factory() as db:
        stored = db.get(CommunityMasterLinkRow, link.id)
        assert stored.link_state == state
        assert stored.merged_into == ("v_9" if state == "merged" else None)


def test_completed_scans_and_user_confirmation_refresh_local_verification(setup):
    service, factory, masters = setup
    master_route(service, "v_1", {None: master_response("v_1", 1, ELSEWHERE)})
    link = download(masters)
    assert rows(factory, link.id) == {"f_far": ("pending", False)}
    with factory() as db:
        db.add(
            ScanFindingRow(
                scan_id="scan",
                category="violence",
                subcategory="kicking",
                start_seconds=500.5,
                end_seconds=502.0,
                source="video",
            )
        )
        db.get(ScanRow, "scan").status = "scanning"
        db.commit()
        update_scan_status(db, "scan", "complete")
    assert rows(factory, link.id) == {"f_far": ("pending", True)}

    with factory() as db:
        set_filter_decision(db, OWNER, link.id, "f_far", "verify")
        db.commit()
    assert rows(factory, link.id)["f_far"] == ("verified", True)
