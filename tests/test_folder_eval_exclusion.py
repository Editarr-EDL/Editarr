"""Symlinks must not bypass the `video/nude/` exclusion."""

from __future__ import annotations

import pytest

from scanner.evaluation.folder_eval import frames as frames_module
from scanner.evaluation.folder_eval.frames import extract_item
from scanner.evaluation.folder_eval.inventory import build_inventory
from scanner.evaluation.folder_eval.labels import LabelRules
from scanner.evaluation.folder_eval.paths import ensure_allowed


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _link(link, target):
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


@pytest.fixture
def root(tmp_path):
    data_root = tmp_path / "data"
    _write(data_root / "video/nude/a.mp4")
    _link(data_root / "video/clips/alias.mp4", data_root / "video/nude/a.mp4")
    _link(data_root / "video/clips/upper.mp4", data_root / "video/Nude/a.mp4")
    _write(data_root / "video/clips/real.mp4")
    film = _write(tmp_path / "elsewhere/film.mkv")
    _link(data_root / "films/Film (2000)/film.mkv", film)
    return data_root


def test_symlinks_into_excluded_folder_are_refused(root):
    for alias in ("video/clips/alias.mp4", "video/clips/upper.mp4"):
        with pytest.raises(PermissionError):
            ensure_allowed(alias, root)
    ensure_allowed("video/clips/real.mp4", root)
    ensure_allowed("films/Film (2000)/film.mkv", root)


def test_inventory_skips_and_never_probes_symlinks_into_excluded_folder(root):
    probed = []
    payload = build_inventory(
        root, LabelRules.load(), probe=lambda path: probed.append(path.name) or 8.0
    )
    assert [item["path"] for item in payload["items"]] == ["video/clips/real.mp4"]
    assert probed == ["real.mp4"]


def test_frame_extraction_refuses_symlinks_into_excluded_folder(root, monkeypatch):
    monkeypatch.setattr(frames_module, "_ffmpeg", lambda *_a, **_k: pytest.fail("decoded"))
    item = {"id": "x", "path": "video/clips/alias.mp4", "frames": [
        {"n": 0, "t": 1.0, "file": "frames/x/0.jpg"},
    ]}
    with pytest.raises(PermissionError):
        extract_item(str(root), item)
    assert not (root / "_results/frames/x").exists()
