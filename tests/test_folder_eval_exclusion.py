"""Symlinks must not bypass the `video/nude/` exclusion."""

from __future__ import annotations

import json

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


@pytest.mark.parametrize("via_link", [False, True])
def test_films_in_the_excluded_folder_fail_before_linking_or_probing(
    root, tmp_path, monkeypatch, via_link
):
    from scanner.evaluation.folder_eval import films
    from scanner.evaluation.folder_eval.film_sources import FilmSourceError, FilmSources
    from scanner.evaluation.folder_eval.paths import Layout

    layout = Layout(root)
    layout.results.mkdir(parents=True)
    layout.inventory.write_text('{"items": []}', encoding="utf-8")
    truth = tmp_path / "csv"
    truth.mkdir()
    (truth / "movies.csv").write_text(
        "imdb_id,name,title_main,year,duration_sec\ntt1,Alpha,Alpha,2001,30\n", encoding="utf-8"
    )
    (truth / "tag_timestamps.csv").write_text(
        "imdb_id,category,tag_name,start_approx,end_approx\n", encoding="utf-8"
    )
    config = tmp_path / "films.json"
    config.write_text('{"films": [{"title": "Alpha", "year": 2001}]}', encoding="utf-8")
    film = root / "video/nude/a.mp4"
    if via_link:
        film = tmp_path / "outside.mp4"
        film.symlink_to(root / "video/nude/a.mp4")
    monkeypatch.setattr(films, "probe_film", lambda _path: pytest.fail("probed"))

    sources = FilmSources(truth, {"tt1": film}, {})
    with pytest.raises(FilmSourceError, match="excluded folder"):
        films.build_films(layout, sources, config)
    assert not (root / "films/Alpha (2001)").exists()
    assert layout.inventory.read_text() == '{"items": []}'


def test_a_refused_item_is_recorded_without_aborting_the_batch(root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from scanner.evaluation.folder_eval.paths import Layout

    layout = Layout(root)
    layout.results.mkdir(parents=True)
    items = [
        {"id": "bad", "path": "video/clips/alias.mp4", "frames": [
            {"n": 0, "t": 1.0, "file": "frames/bad/0.jpg"}]},
        {"id": "good", "path": "video/clips/real.mp4", "frames": [
            {"n": 0, "t": 1.0, "file": "frames/good/0.jpg"}]},
    ]
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")

    def fake_ffmpeg(_source, target, *_args):
        target.write_bytes(b"x")
        return True, None

    monkeypatch.setattr(frames_module, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(frames_module, "_ffmpeg", fake_ffmpeg)
    errors = frames_module.extract_frames(layout, workers=1)
    assert list(errors) == ["bad"] and errors["bad"][0].startswith("PermissionError")
    assert (layout.results / "frames/good/0.jpg").is_file()


def _scoring_items(root):
    from scanner.evaluation.folder_eval.paths import Layout

    layout = Layout(root)
    items = []
    for identifier, path in (("bad", "video/clips/alias.mp4"), ("good", "video/clips/real.mp4")):
        frame = {"n": 0, "t": 1.0, "file": f"frames/{identifier}/0.jpg"}
        (layout.results / frame["file"]).parent.mkdir(parents=True, exist_ok=True)
        (layout.results / frame["file"]).write_bytes(b"x")
        items.append(({"id": identifier, "path": path}, [frame]))
    return layout, items


def test_batch_backends_never_receive_excluded_items(root):
    from scanner.evaluation.folder_eval.scoring import scored

    layout, pending = _scoring_items(root)
    batches = []

    class Batch:
        model_id = "batch"

        def score_items(self, paths):
            batches.append([[path.parent.name for path in frames] for frames in paths])
            return [[{"scores": {"violence": 0.9}}] for _ in paths]

    results = scored(Batch(), layout, pending, {}, {}, 1)
    records = {item["id"]: record for item, _f, record in results}
    assert batches == [[["good"]]]
    assert records["bad"]["status"] == "error"
    assert records["bad"]["error"].startswith("PermissionError")
    assert records["good"]["status"] == "ok"


def test_single_item_backends_record_excluded_items_as_errors(root):
    from scanner.evaluation.folder_eval.scoring import scored

    layout, pending = _scoring_items(root)
    seen = []

    class Single:
        model_id = "single"

        def score_frames(self, paths):
            seen.extend(path.parent.name for path in paths)
            return [{"scores": {}} for _ in paths]

    results = scored(Single(), layout, pending, {}, {}, 1)
    records = {item["id"]: record for item, _f, record in results}
    assert seen == ["good"] and records["bad"]["status"] == "error"


def test_planted_frame_symlinks_are_never_scored_and_get_re_extracted(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval.frames import frames_present
    from scanner.evaluation.folder_eval.paths import Layout
    from scanner.evaluation.folder_eval.scoring import scored

    layout = Layout(tmp_path)
    secret = tmp_path / "outside.jpg"
    secret.write_bytes(b"private")
    (tmp_path / "video/x").mkdir(parents=True)
    (tmp_path / "video/x/a.mp4").write_bytes(b"x")
    item = {"id": "a", "path": "video/x/a.mp4", "frames": [
        {"n": 0, "t": 1.0, "file": "frames/a/0.jpg"}, {"n": 1, "t": 2.0, "file": "frames/b/0.jpg"},
    ]}
    (layout.results / "frames/a").mkdir(parents=True)
    (layout.results / "frames/a/0.jpg").symlink_to(secret)
    (layout.results / "frames/b").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "0.jpg").write_bytes(b"private")
    assert frames_present(layout, item) == []

    seen = []

    class Backend:
        model_id = "m"

        def score_frames(self, paths):
            seen.extend(paths)
            return [{"scores": {}} for _ in paths]

    ((_item, _frames, record),) = scored(Backend(), layout, [(item, item["frames"])], {}, {}, 1)
    assert seen == [] and record["status"] == "error" and "frames cache" in record["error"]

    def fake_ffmpeg(_source, target, *_args):
        target.write_bytes(b"jpeg")
        return True, None

    monkeypatch.setattr(frames_module, "_ffmpeg", fake_ffmpeg)
    _id, written, errors, _times = extract_item(str(tmp_path), item)
    assert (written, errors) == (2, [])
    assert secret.read_bytes() == b"private" and (tmp_path / "0.jpg").read_bytes() == b"private"
    assert [frame["n"] for frame in frames_present(layout, item)] == [0, 1]
