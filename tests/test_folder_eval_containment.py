"""Writes and reads under `_results/` never follow a symlink out of the results tree."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scanner.evaluation.folder_eval.paths import (
    Layout,
    cached_frame_ok,
    open_append,
    write_json,
)


@pytest.fixture
def outside(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep.txt").write_text("original")
    return target


def _root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    return Layout(root)


def test_a_linked_results_tree_is_never_written_or_trusted(tmp_path, outside):
    from scanner.evaluation.folder_eval.viewer_install import install_viewer

    layout = _root(tmp_path)
    layout.results.symlink_to(outside, target_is_directory=True)
    (outside / "frames" / "a").mkdir(parents=True)
    (outside / "frames" / "a" / "0.jpg").write_bytes(b"planted")
    for write in (
        lambda: write_json(layout.summary, {}),
        lambda: open_append(layout.model_jsonl("m")),
        lambda: install_viewer(layout),
    ):
        with pytest.raises(PermissionError):
            write()
    assert not cached_frame_ok(layout.results, "a", "frames/a/0.jpg")
    assert sorted(path.name for path in outside.iterdir()) == ["frames", "keep.txt"]


@pytest.mark.parametrize("linked", ["models", "frames", "subsets"])
def test_a_linked_folder_inside_results_is_refused(tmp_path, outside, linked):
    layout = _root(tmp_path)
    layout.results.mkdir()
    (layout.results / linked).symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        write_json(layout.results / linked / "x.json", {})
    with pytest.raises(PermissionError):
        open_append(layout.results / linked / "x.jsonl")
    assert [path.name for path in outside.iterdir()] == ["keep.txt"]


def test_links_at_the_destination_are_replaced_or_refused_never_followed(tmp_path, outside):
    layout = _root(tmp_path)
    layout.models.mkdir(parents=True)
    layout.summary.symlink_to(outside / "keep.txt")
    write_json(layout.summary, {"ok": True})
    assert not layout.summary.is_symlink()
    assert json.loads(layout.summary.read_text()) == {"ok": True}
    layout.model_jsonl("m").symlink_to(outside / "keep.txt")
    with pytest.raises(OSError):
        os.close(open_append(layout.model_jsonl("m")))
    assert (outside / "keep.txt").read_text() == "original"


def test_corrections_log_never_appends_through_a_link(tmp_path, outside):
    from scanner.evaluation.folder_eval.corrections import save_correction

    layout = _root(tmp_path)
    layout.results.mkdir()
    layout.inventory.write_text(json.dumps({"items": [{"id": "a", "expected": []}]}))
    (layout.results / "corrections.log.jsonl").symlink_to(outside / "keep.txt")
    with pytest.raises(OSError):
        save_correction(layout, "a", "violence", True)
    assert (outside / "keep.txt").read_text() == "original"


def test_viewer_install_replaces_linked_files(tmp_path, outside):
    from scanner.evaluation.folder_eval.viewer_install import install_viewer

    layout = _root(tmp_path)
    layout.results.mkdir()
    (layout.results / "app.js").symlink_to(outside / "keep.txt")
    install_viewer(layout)
    assert not (layout.results / "app.js").is_symlink()
    assert (outside / "keep.txt").read_text() == "original"


def test_ground_truth_copy_replaces_linked_files(tmp_path, outside):
    from scanner.evaluation.folder_eval.films import _copy_ground_truth

    source = tmp_path / "csv"
    source.mkdir()
    (source / "movies.csv").write_text("imdb_id,name\ntt1,A\ntt2,B\n")
    destination = tmp_path / "copy"
    destination.mkdir()
    (destination / "movies.csv").symlink_to(outside / "keep.txt")
    _copy_ground_truth(source, destination, {"tt1"})
    assert (destination / "movies.csv").read_text().splitlines() == ["imdb_id,name", "tt1,A"]
    assert (outside / "keep.txt").read_text() == "original"


def test_server_refuses_a_linked_results_tree(tmp_path, outside):
    import http.client
    import threading

    from scanner.evaluation.folder_eval.server import ReviewServer

    layout = _root(tmp_path)
    (outside / "inventory.json").write_text('{"items": []}')
    layout.results.symlink_to(outside, target_is_directory=True)
    server = ReviewServer(layout, port=0, debounce_s=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        host = {"Host": f"127.0.0.1:{server.port}"}
        connection.request("GET", "/_results/keep.txt", headers=host)
        response = connection.getresponse()
        assert response.status == 404 and b"original" not in response.read()
    finally:
        server.shutdown()
        server.server_close()


def test_a_planted_lock_link_is_refused_without_touching_its_target(tmp_path, outside):
    from scanner.evaluation.folder_eval.run_lock import lock_path, model_lock

    layout = _root(tmp_path)
    layout.models.mkdir(parents=True)
    lock_path(layout, "m").symlink_to(outside / "keep.txt")
    with pytest.raises(PermissionError), model_lock(layout, "m"):
        pytest.fail("locked through a link")
    assert (outside / "keep.txt").read_text() == "original"


def test_open_regular_without_o_nofollow(tmp_path, outside, monkeypatch):
    from scanner.evaluation.folder_eval import paths

    layout = _root(tmp_path)
    layout.models.mkdir(parents=True)
    target = layout.model_jsonl("m")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    target.symlink_to(outside / "keep.txt")
    with pytest.raises(PermissionError):
        paths.open_append(target)
    # A swap between the check and the open (simulated by replacing os.open) is caught
    # before anything is written.
    target.unlink()
    real_open = os.open

    def swapping_open(path, flags, mode=0o777):
        Path(path).unlink(missing_ok=True)
        Path(path).symlink_to(outside / "keep.txt")
        return real_open(path, flags, mode)

    monkeypatch.setattr(paths.os, "open", swapping_open)
    with pytest.raises(PermissionError):
        paths.open_append(target)
    assert (outside / "keep.txt").read_text() == "original"
