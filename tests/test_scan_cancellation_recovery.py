"""A cancellation remains pending until the previous attempt's writer leaves."""

from threading import Event

import pytest

from scanner import api as root
from scanner.db import ScanRow
from scanner.http import api_queue, scan_jobs_api
from scanner.persistence.repository.repository_queue import cancel_scan_queue_item
from scanner.pipeline.visual_checkpoints import visual_checkpoint_dir
from scanner.runtime.worker_checkpoint_lease import checkpoint_writer_lease
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_scan_access_http import app as app


@pytest.mark.parametrize("finish", ["retry", "scheduler"])
def test_cancellation_retains_checkpoints_while_orphaned_writer_is_alive(
    app, monkeypatch, tmp_path, finish,
):
    scan_id, queue_id = app.seed_scan(USER, "user-interrupted", status="orphaned")
    token = "interrupted-attempt"
    monkeypatch.setattr(root.settings, "data_dir", tmp_path)
    monkeypatch.setattr(root, "get_session_factory", lambda: app.accounts.factory)
    with app.accounts.factory() as db:
        db.get(ScanRow, scan_id).worker_token = token
        db.commit()
    checkpoint = visual_checkpoint_dir(scan_id, "fixture-media", {}, {}) / "retained.json"
    checkpoint.write_text("[]", encoding="utf-8")
    route = f"/api/scans/queue/{queue_id}"
    headers = app.accounts.unsafe_cookie(USER)

    stop = Event()
    probe = root.PROCESS_CONTROL.attempt_may_be_running

    def probe_once(*args, **kwargs):
        running = probe(*args, **kwargs)
        stop.set()
        return running

    monkeypatch.setattr(root, "SCAN_QUEUE_STOP_EVENT", stop)
    monkeypatch.setattr(root.PROCESS_CONTROL, "attempt_may_be_running", probe_once)
    with checkpoint_writer_lease(tmp_path, scan_id, token):
        assert app.send("DELETE", route, headers).json() == {"cancelled": True, "cancelling": True}
        stop.clear()
        root._scan_queue_worker()
        assert app.scan(scan_id).status == "cancelling"
        assert app.scan(scan_id).worker_token == token
        assert scan_id in app.queued()
        assert checkpoint.is_file()

    if finish == "retry":
        assert app.send("DELETE", route, headers).json() == {"cancelled": True, "cancelling": False}
    else:
        stop.clear()
        root._scan_queue_worker()
    assert app.scan(scan_id).status == "cancelled"
    assert app.scan(scan_id).worker_token is None
    assert scan_id not in app.queued()
    assert not checkpoint.exists()


def test_cancellation_does_not_touch_a_queue_slot_reused_after_admission(app, monkeypatch):
    scan_id, queue_id = app.seed_scan(USER, "original-job")
    load_worker = scan_jobs_api.scan_worker_process
    replacements = []

    def replace_queue_slot(db, observed_queue_id):
        assert observed_queue_id == queue_id
        with app.accounts.factory() as writer:
            assert cancel_scan_queue_item(writer, queue_id) == (scan_id, False)
        replacement, new_queue_id = app.seed_scan(PEER_USER, "different-job")
        assert new_queue_id == queue_id
        replacements.append(replacement)
        return load_worker(db, observed_queue_id)

    monkeypatch.setattr(scan_jobs_api, "scan_worker_process", replace_queue_slot)
    response = app.send("DELETE", f"/api/scans/queue/{queue_id}", app.accounts.unsafe_cookie(USER))
    assert response.status_code == 404
    assert app.scan(replacements[0]).status == "queued"


def test_unproven_worker_identity_preserves_its_control_endpoint(monkeypatch):
    monkeypatch.setattr(root, "_worker_identity", lambda *_: None)
    monkeypatch.setattr(
        root, "cleanup_worker_control_endpoint", lambda *_: pytest.fail("unproven worker")
    )
    assert not api_queue._terminate_scan_worker("scan", 42, 42, "attempt")
