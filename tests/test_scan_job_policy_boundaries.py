"""Observed linked media-policy changes fence findings without an account revision."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from scanner import worker
from scanner.db import MediaItemRow, ScanFindingRow, ScanRow
from scanner.integrations.jellyfin_user_snapshot import ItemAccess
from scanner.pipeline import runner
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from scanner.security.scan_jobs import authority, media
from tests.media_access_support import FAMILY, FAMILY_FILM, JELLYFIN_ITEMS, jellyfin_user, resolve
from tests.multi_user_access_support import USER
from tests.scan_access_http import ScanApp
from tests.scan_job_boundary_support import finding, prepare_worker
from tests.scan_job_boundary_support import synthetic_video as synthetic_video
from tests.test_jellyfin_account_evidence import ScriptedAdapter
from tests.test_jellyfin_account_evidence import clock as clock
from tests.test_jellyfin_account_evidence import linked as linked


@pytest.mark.parametrize("delay", [1, 29, 30, 31])
@pytest.mark.parametrize("revoke", [False, True])
def test_linked_policy_fences_findings_without_a_permission_revision(
    linked, clock, monkeypatch, synthetic_video, tmp_path, delay, revoke
):
    folder = tmp_path / "worker-media"
    folder.mkdir()
    app = ScanApp(None, linked, folder)
    scan_id = prepare_worker(app, monkeypatch, synthetic_video)
    path = Path(resolve(JELLYFIN_ITEMS[FAMILY_FILM].path))
    path.write_bytes(synthetic_video.read_bytes())
    with linked.factory() as db:
        scan = db.get(ScanRow, scan_id)
        scan.resolved_local_path = str(path)
        item = db.get(MediaItemRow, scan.media_item_id)
        item.jellyfin_id, item.library_id = FAMILY_FILM, FAMILY
        db.commit()
    monkeypatch.setattr(media, "_resolver", lambda _db: resolve)
    restricted = [False]

    class PolicyAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            snapshot = super().item_access(identity, item_ids)
            if restricted[0]:
                return replace(
                    snapshot, decisions={item: ItemAccess.RESTRICTED for item in item_ids}
                )
            return snapshot

    class FixedDatetime:
        @staticmethod
        def now(_zone):
            return clock()

    monkeypatch.setattr(authority, "datetime", FixedDatetime)
    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: PolicyAdapter(clock))
    monkeypatch.setattr(evidence_cache, "_copies", {})
    monkeypatch.setattr(evidence_cache, "_items", {})
    revision = app.revision(USER)

    def detector(*_args, **_kwargs):
        if revoke:
            snapshot = jellyfin_user("Casey")
            denied = replace(
                snapshot,
                media=replace(snapshot.media, all_libraries=False, library_ids=frozenset()),
            )
            with linked.factory() as db:
                store.apply(db, USER, denied, reads.begin_read(db, clock))
            restricted[0] = True
        clock.now += timedelta(seconds=delay)
        return [finding(scan_id)]

    monkeypatch.setattr(runner, "_scan_visual_isolated", detector)
    assert worker.run_scan_worker(scan_id, "worker-token") == 0
    with linked.factory() as db:
        count = db.query(ScanFindingRow).filter_by(scan_id=scan_id).count()
    row = app.scan(scan_id)
    assert app.revision(USER) == revision
    assert row.initiator_id == USER and row.worker_token is None
    assert app.queued() == []
    assert (row.status, count) == (("failed", 0) if revoke else ("complete", 1))
