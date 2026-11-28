"""Tests for ground-truth corrections and the review server."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from scanner.evaluation.folder_eval.corrections import (
    CorrectionError,
    apply_corrections,
    effective_expected,
    load_corrections,
    normalise,
    save_correction,
    validate_request,
)
from scanner.evaluation.folder_eval.paths import Layout
from scanner.evaluation.folder_eval.summary import summarize, summarize_model


def test_normalise_family_false_drops_true_tokens_and_token_true_drops_false_family():
    item = {"violence/punching": True, "violence/murder": False, "fear/fire": True}
    assert normalise(item, "violence", False) == {
        "violence/murder": False, "fear/fire": True, "violence": False,
    }
    assert normalise({"violence": False}, "violence/punching", True) == {
        "violence/punching": True,
    }
    assert normalise({"violence": True}, "violence", None) == {}


def test_effective_expected_implies_and_removes_families():
    base = ["violence", "violence/punching"]
    assert effective_expected(base, {"violence/punching": False}) == ["violence"]
    assert effective_expected(base, {"violence": False}) == []
    assert effective_expected([], {"sex/kissing": True}) == ["sex", "sex/kissing"]
    assert effective_expected(base, {}) == base


def test_apply_corrections_marks_corrected_items_scored():
    items = [
        {"id": "a", "expected": ["violence"], "scored": False},
        {"id": "b", "expected": [], "scored": False},
    ]
    applied = apply_corrections(items, {"items": {"a": {"violence": False}}})
    assert applied[0]["expected"] == [] and applied[0]["scored"] and applied[0]["corrected"]
    assert applied[1] == items[1]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"item_id": "zzz", "key": "violence", "value": True}, "unknown item_id"),
        ({"item_id": "a", "key": "violence/nope", "value": True}, "key must be"),
        ({"item_id": "a", "key": "violence/violence", "value": True}, "key must be"),
        ({"item_id": "a", "key": "violence", "value": 1}, "value must be"),
        ({"item_id": "a", "key": "violence"}, "value is required"),
        ({"item_id": "a", "key": "violence", "value": True, "x": 1}, "unknown fields"),
        ([], "JSON object"),
    ],
)
def test_validate_request_rejects(body, message):
    with pytest.raises(CorrectionError, match=message):
        validate_request(body, {"a"})


def test_corrections_flip_fp_to_tp_and_fn_to_tn():
    items = [
        {"id": "fp", "path": "video/x/a.mp4", "expected": [], "scored": True},
        {"id": "fn", "path": "video/x/b.mp4", "expected": ["violence"], "scored": True},
    ]
    results = {
        "fp": {"id": "fp", "status": "ok", "frames": [{}], "flagged": ["violence"]},
        "fn": {"id": "fn", "status": "ok", "frames": [{}], "flagged": []},
    }
    meta = {"supports": ["violence"]}
    before = summarize_model(items, results, meta)["families"]["violence"]
    assert (before["tp"], before["fp"], before["fn"], before["tn"]) == (0, 1, 1, 0)
    corrected = apply_corrections(
        items, {"items": {"fp": {"violence": True}, "fn": {"violence": False}}}
    )
    after = summarize_model(corrected, results, meta)["families"]["violence"]
    assert (after["tp"], after["fp"], after["fn"], after["tn"]) == (1, 0, 0, 1)


@pytest.fixture
def data_root(tmp_path):
    layout = Layout(tmp_path)
    items = [
        {"id": "a", "path": "video/x/a.mp4", "folder": "video/x", "kind": "video",
         "expected": [], "scored": True, "frames": []},
        {"id": "b", "path": "video/x/b.mp4", "folder": "video/x", "kind": "video",
         "expected": ["violence"], "scored": False, "frames": []},
    ]
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    layout.models.mkdir()
    layout.model_jsonl("m").write_text("".join(json.dumps(line) + "\n" for line in [
        {"id": "a", "status": "ok", "frames": [{}], "flagged": ["violence"]},
        {"id": "b", "status": "ok", "frames": [{}], "flagged": []},
    ]), encoding="utf-8")
    layout.model_meta("m").write_text(json.dumps({"supports": ["violence"]}), encoding="utf-8")
    (tmp_path / "video" / "x").mkdir(parents=True)
    (tmp_path / "video" / "x" / "a.mp4").write_bytes(bytes(range(100)))
    return tmp_path


def test_save_correction_writes_file_and_audit_log(data_root):
    layout = Layout(data_root)
    save_correction(layout, "a", "violence", True, "visible punch")
    save_correction(layout, "a", "violence", None)
    assert load_corrections(layout)["items"] == {}
    log = [json.loads(line) for line in (layout.results / "corrections.log.jsonl").open()]
    assert [(e["value"], e["previous"]) for e in log] == [(True, None), (None, True)]
    assert log[0]["note"] == "visible punch"


@pytest.fixture
def server(data_root):
    from scanner.evaluation.folder_eval.server import ReviewServer

    instance = ReviewServer(Layout(data_root), port=0, debounce_s=0.05)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()


def _request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    merged = {"Content-Type": "application/json"} if data is not None else {}
    merged.update(headers or {})
    connection.request(method, path, body=data, headers=merged)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response, payload


def test_server_corrections_happy_path_and_summary_refresh(server, data_root):
    response, payload = _request(
        server, "POST", "/_api/corrections",
        {"item_id": "b", "key": "violence/punching", "value": True},
        {"Origin": f"http://127.0.0.1:{server.port}"},
    )
    body = json.loads(payload)
    assert response.status == 200 and body["ok"] and body["summary_pending"]
    assert body["corrections"] == {"violence/punching": True}
    assert body["effective_expected"] == ["violence", "violence/punching"]

    assert server.summarizer.wait_idle()
    summary = json.loads((data_root / "_results" / "summary.json").read_text())
    assert summary["corrections"]["count"] == 1
    # Item b was unscored; the correction makes it count (as an FN for model m).
    assert summary["models"]["m"]["families"]["violence"]["fn"] == 1

    _, status = _request(server, "GET", "/_api/status")
    status = json.loads(status)
    assert status["writable"] and status["corrections"] == 1 and not status["summarizing"]
    _, stored = _request(server, "GET", "/_api/corrections")
    assert json.loads(stored)["items"] == {"b": {"violence/punching": True}}


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        ({"item_id": "nope", "key": "violence", "value": True}, {}, 400),
        ({"item_id": "a", "key": "violence", "value": True}, {"Content-Type": "text/plain"}, 415),
        ({"item_id": "a", "key": "violence", "value": True}, {"Origin": "http://evil.test"}, 403),
        ({"item_id": "a", "key": "violence", "value": True}, {"Host": "evil.test"}, 403),
        ({"item_id": "a", "key": "violence", "value": True},
         {"Sec-Fetch-Site": "cross-site"}, 403),
        ({"item_id": "a", "key": "violence", "value": True, "note": "x" * 17000}, {}, 413),
    ],
)
def test_server_rejects_invalid_and_cross_origin_posts(server, data_root, body, headers, status):
    response, payload = _request(server, "POST", "/_api/corrections", body, headers)
    assert response.status == status and json.loads(payload)["ok"] is False
    assert not (data_root / "_results" / "corrections.json").exists()


def test_server_static_range_and_traversal(server, data_root):
    response, payload = _request(server, "GET", "/video/x/a.mp4", headers={"Range": "bytes=10-19"})
    assert response.status == 206 and payload == bytes(range(10, 20))
    assert response.getheader("Content-Range") == "bytes 10-19/100"
    response, payload = _request(server, "GET", "/video/x/a.mp4", headers={"Range": "bytes=-5"})
    assert response.status == 206 and payload == bytes(range(95, 100))
    response, _ = _request(server, "GET", "/video/x/a.mp4", headers={"Range": "bytes=200-"})
    assert response.status == 416
    response, payload = _request(server, "GET", "/video/x/a.mp4")
    assert response.status == 200 and len(payload) == 100
    assert response.getheader("Accept-Ranges") == "bytes"
    response, _ = _request(server, "GET", "/../../etc/passwd")
    assert response.status == 404
    (data_root / "video" / "nude").mkdir()
    (data_root / "video" / "nude" / "x.jpg").write_bytes(b"x")
    response, _ = _request(server, "GET", "/video/nude/x.jpg")
    assert response.status == 404


def test_summarize_reports_corrections(data_root):
    layout = Layout(data_root)
    save_correction(layout, "a", "violence", True)
    payload = summarize(layout)
    assert payload["corrections"]["count"] == 1
    assert payload["models"]["m"]["families"]["violence"]["tp"] == 1


def test_server_rejects_foreign_host_for_static_files(server):
    response, _ = _request(server, "GET", "/video/x/a.mp4", headers={"Host": "evil.test"})
    assert response.status == 403
    response, _ = _request(server, "HEAD", "/video/x/a.mp4", headers={"Host": "evil.test:80"})
    assert response.status == 403
    local = {"Host": f"localhost:{server.port}"}
    response, _ = _request(server, "GET", "/video/x/a.mp4", headers=local)
    assert response.status == 200


def test_server_blocks_symlinks_into_excluded_folder(server, data_root):
    nude = data_root / "video" / "nude"
    nude.mkdir()
    (nude / "x.jpg").write_bytes(b"x")
    (data_root / "video" / "x" / "alias.jpg").symlink_to(nude / "x.jpg")
    (data_root / "films").mkdir()
    (data_root / "films" / "dir").symlink_to(nude, target_is_directory=True)
    for path in ("/video/x/alias.jpg", "/films/dir/x.jpg", "/films/dir/"):
        response, _ = _request(server, "GET", path)
        assert response.status == 404, path


def test_summarize_recomputes_when_corrections_change_midway(data_root, monkeypatch):
    from scanner.evaluation.folder_eval import summary as summary_module

    layout = Layout(data_root)
    real_build = summary_module.build_summary
    calls = []

    def racing_build(layout_arg, corrections):
        calls.append(corrections.get("updated_at"))
        payload = real_build(layout_arg, corrections)
        if len(calls) == 1:
            save_correction(layout, "a", "violence", True)  # a POST lands mid-summary
        return payload

    monkeypatch.setattr(summary_module, "build_summary", racing_build)
    payload = summary_module.summarize(layout)

    latest = load_corrections(layout)["updated_at"]
    assert calls == [None, latest]
    written = json.loads(layout.summary.read_text())
    assert written["corrections"] == {"count": 1, "updated_at": latest} == payload["corrections"]


def test_server_requeues_summary_when_it_used_stale_corrections(server, monkeypatch):
    from scanner.evaluation.folder_eval import server as server_module

    save_correction(server.layout, "a", "violence", True)
    stale = {"corrections": {"count": 0, "updated_at": None}}
    monkeypatch.setattr(server_module, "summarize", lambda _layout: stale)
    requests = []
    monkeypatch.setattr(server.summarizer, "request", lambda: requests.append(1))
    server._refresh_summary()
    assert requests == [1]


def test_summary_scores_general_token_aliases_as_their_family():
    items = [{"id": "a", "path": "video/x/a.mp4", "expected": ["violence"], "scored": True}]
    results = {"a": {"id": "a", "status": "ok", "flagged": ["violence", "violence/violence"],
                     "frames": [{"n": 0, "scores": {}}]}}
    summary = summarize_model(items, results, {"supports": ["violence", "violence/violence"]})
    assert summary["families"]["violence"]["tp"] == 1 and summary["tokens"] == {}
    assert set(summary["by_folder"]["video/x"]) == {"violence"}


def test_summarizer_stop_cancels_pending_waits_for_active_and_joins():
    import time

    from scanner.evaluation.folder_eval.server_state import Summarizer

    runs, release = [], threading.Event()
    pending = Summarizer(lambda: runs.append("pending"), delay_s=60)
    pending.request()
    pending.stop(timeout_s=5)
    assert runs == [] and not pending._thread.is_alive()
    pending.request()
    assert not pending.busy

    def slow():
        runs.append("started")
        release.wait(5)
        runs.append("finished")

    active = Summarizer(slow, delay_s=0)
    active.request()
    while runs != ["started"]:
        time.sleep(0.01)
    stopper = threading.Thread(target=active.stop)
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive()  # waits for the active run
    release.set()
    stopper.join(5)
    assert runs == ["started", "finished"] and not active._thread.is_alive()


def test_server_close_stops_the_summarizer(server):
    server.summarizer.request()
    server.shutdown()
    server.server_close()
    assert not server.summarizer._thread.is_alive()


def test_port_in_use_raises_the_bind_error(data_root):
    import socket

    from scanner.evaluation.folder_eval.server import ReviewServer

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(OSError):
            ReviewServer(Layout(data_root), port=taken.getsockname()[1])


def test_server_refuses_symlinks_in_the_frames_cache(server, data_root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.jpg"
    outside.write_bytes(b"private")
    frames = data_root / "_results" / "frames"
    (frames / "real").mkdir(parents=True)
    (frames / "real" / "0.jpg").write_bytes(b"jpeg")
    (frames / "planted").mkdir()
    (frames / "planted" / "0.jpg").symlink_to(outside)
    (frames / "linked").symlink_to(outside.parent, target_is_directory=True)
    (frames / "inner").mkdir()
    (frames / "inner" / "0.jpg").symlink_to(frames / "real" / "0.jpg")
    response, body = _request(server, "GET", "/_results/frames/real/0.jpg")
    assert response.status == 200 and body == b"jpeg"
    for path in ("/_results/frames/planted/0.jpg", "/_results/frames/linked/secret.jpg",
                 "/_results/frames/inner/0.jpg"):
        response, body = _request(server, "GET", path)
        assert response.status == 404 and b"private" not in body, path


def test_a_failed_audit_append_rolls_the_correction_back(data_root, monkeypatch):
    from scanner.evaluation.folder_eval import corrections as module

    layout = Layout(data_root)
    save_correction(layout, "a", "violence", True)
    before = (layout.results / "corrections.json").read_text()

    def failing_write(_descriptor, _data):
        raise OSError("disk full")

    monkeypatch.setattr(module.os, "write", failing_write)
    with pytest.raises(module.CorrectionNotSaved, match="disk full"):
        save_correction(layout, "a", "violence", False)
    assert (layout.results / "corrections.json").read_text() == before

    real_write_json = module.write_json
    calls = []

    def write_once(path, payload):
        calls.append(path)
        if len(calls) > 1:
            raise OSError("read-only")
        real_write_json(path, payload)

    monkeypatch.setattr(module, "write_json", write_once)
    with pytest.raises(module.CorrectionUnaudited):
        save_correction(layout, "b", "violence", True)
    assert "b" in json.loads((layout.results / "corrections.json").read_text())["items"]


def test_api_reports_failure_only_when_nothing_was_committed(server, data_root, monkeypatch):
    from scanner.evaluation.folder_eval import corrections as module
    from scanner.evaluation.folder_eval import server as server_module

    requested = []
    monkeypatch.setattr(server.summarizer, "request", lambda: requested.append(1))
    log_file = data_root / "_results" / "corrections.log.jsonl"
    log_file.unlink(missing_ok=True)
    log_file.symlink_to(data_root / "video" / "x" / "a.mp4")
    body = {"item_id": "a", "key": "violence", "value": True}
    response, payload = _request(server, "POST", "/_api/corrections", body)
    assert response.status == 500 and not json.loads(payload)["ok"]
    assert not (data_root / "_results" / "corrections.json").exists() and requested == []

    log_file.unlink()

    def unaudited(*_args, **_kwargs):
        layout = Layout(data_root)
        payload = module.load_corrections(layout)
        payload["items"]["a"] = {"violence": True}
        module.write_json(module.corrections_path(layout), payload)
        raise module.CorrectionUnaudited("audit entry and restore failed")

    monkeypatch.setattr(server_module, "save_correction", unaudited)
    response, payload = _request(server, "POST", "/_api/corrections", body)
    result = json.loads(payload)
    assert response.status == 200 and result["ok"] and result["audit_logged"] is False
    assert result["corrections"] == {"violence": True} and requested == [1]
