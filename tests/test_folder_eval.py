"""Unit tests for the folder-structure evaluation harness."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scanner.evaluation.folder_eval import inventory as inventory_module
from scanner.evaluation.folder_eval.inventory import build_inventory, plan_frame_times
from scanner.evaluation.folder_eval.labels import LabelRules
from scanner.evaluation.folder_eval.paths import Layout, ensure_allowed, is_excluded
from scanner.evaluation.folder_eval.results import aggregate, load_results
from scanner.evaluation.folder_eval.summary import summarize_model
from scanner.evaluation.folder_eval.viewer_install import install_viewer
from tests.folder_eval_support import clip


@pytest.fixture(scope="module")
def rules() -> LabelRules:
    return LabelRules.load()


@pytest.mark.parametrize(
    ("path", "expected", "scored"),
    [
        ("video/ucf_crime/test_videos/Normal/a.mp4", [], True),
        ("video/ucf_crime/test_videos/Shooting/a.mp4", ["violence", "violence/weapons"], True),
        ("video/ucf_crime/test_videos/Arson/a.mp4", ["fear", "fear/fire"], True),
        ("video/ucf_crime/test_videos/Burglary/a.mp4", [], True),
        ("video/hockey_fight/videos/fi12_xvid.avi", ["violence", "violence/punching"], True),
        ("video/hockey_fight/videos/no12_xvid.avi", [], True),
        ("video/movies_fight/videos/noFights/x.avi", [], True),
        ("video/Violent Scene Dataset/Non_Violence(No-V)/a.mp4", [], True),
        ("video/Violent Scene Dataset/Violence_Undetermined_Category/a.mp4", ["violence"], False),
        (
            "video/Violent Scene Dataset/Violence_Level2Extreme(Ex-V)/gore/a.mp4",
            ["fear", "fear/hemophobia", "violence"],
            True,
        ),
        (
            "video/Violent Scene Dataset/Violence_Level1Moderate(Mo-V)/threatening/a.mp4",
            ["violence", "violence/emotionalViolence"],
            True,
        ),
        ("video/Violent Scene Dataset/Fear/Fire/a.mp4", ["fear", "fear/fire"], True),
        ("video/nsfw_dataset_v1/drawings/a.jpg", [], True),
        ("video/nsfw_dataset_v1/hentai/a.jpg", ["nudity", "sex"], True),
        ("video/nsfw_dataset_v1/sexy/a.jpg", [], False),
    ],
)
def test_rules_label_dataset_paths(rules, path, expected, scored):
    label = rules.label(path)
    assert (label.expected, label.scored) == (expected, scored)


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ("A", []),
        ("B1-0-0", ["violence", "violence/punching"]),
        ("B4-0-0", ["violence"]),
        ("B2-G-0", ["fear", "fear/explosion", "violence", "violence/weapons"]),
        ("B6-B2-0", ["fear", "fear/accident", "violence", "violence/weapons"]),
    ],
)
def test_xd_violence_codes_combine(rules, codes, expected):
    label = rules.label(f"video/xd_violence/videos/Movie.2001__#00-01_label_{codes}.mp4")
    assert label.expected == expected
    assert label.scored


def test_unknown_xd_code_is_unscored(rules):
    label = rules.label("video/xd_violence/videos/x_label_B3-0-0.mp4")
    assert not label.scored and label.notes


def test_user_created_token_and_family_folders(rules):
    assert rules.label("video/my_clips/Kissing/a.mp4").expected == ["sex", "sex/kissing"]
    assert rules.label("video/my_clips/drugs/a.png").expected == ["drugs"]
    assert rules.label("video/movies_fight/videos/fights/stabbing/a.avi").expected == [
        "violence",
        "violence/stabbing",
    ]
    unlabeled = rules.label("video/my_clips/misc/a.mp4")
    assert unlabeled.expected == [] and not unlabeled.scored


def test_file_names_do_not_add_tokens(rules):
    assert rules.label("video/hockey_fight/videos/no_murder.avi").expected == []


def test_excluded_folders():
    assert is_excluded("video/nude")
    assert is_excluded("video/nude/sub/a.mp4")
    assert is_excluded("video/Nude/a.mp4")
    assert not is_excluded("video/nsfw_dataset_v1/porn/a.jpg")
    assert not is_excluded("video/nudes_other/a.mp4")
    with pytest.raises(PermissionError):
        ensure_allowed("video/nude/a.mp4")


def test_inventory_never_scans_nude_folder(tmp_path, monkeypatch, rules):
    for relative in [
        "video/nude/a.mp4",
        "video/nude/deeper/b.jpg",
        "video/movies_fight/videos/fights/c.avi",
        "video/.hidden/d.mp4",
        "video/clips/e.gif",
        "video/clips/notes.txt",
    ]:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes(b"x")
    scanned: list[str] = []
    real_scandir = os.scandir

    def tracking_scandir(path):
        scanned.append(Path(path).relative_to(tmp_path).as_posix())
        return real_scandir(path)

    monkeypatch.setattr(inventory_module.os, "scandir", tracking_scandir)
    probed: list[Path] = []
    payload = build_inventory(tmp_path, rules, probe=lambda p: probed.append(p) or 10.0)

    excluded = ("video/nude",)
    assert not any(path.startswith(excluded) for path in scanned)
    assert not any(path.relative_to(tmp_path).as_posix().startswith(excluded) for path in probed)
    paths = [item["path"] for item in payload["items"]]
    assert paths == ["video/clips/e.gif", "video/movies_fight/videos/fights/c.avi"]
    image, video = payload["items"]
    assert image["kind"] == "image" and [f["t"] for f in image["frames"]] == [0.0]
    assert video["expected"] == ["violence"] and len(video["frames"]) == 4
    assert video["frames"][0]["file"] == f"frames/{video['id']}/0.jpg"


def test_frame_plan():
    assert plan_frame_times("video", 8.0) == [1.0, 3.0, 5.0, 7.0]
    assert plan_frame_times("video", 45.0) == [7.5, 22.5, 37.5]
    long_clip = plan_frame_times("video", 3600.0)
    assert len(long_clip) == 24 and long_clip[-1] < 3600.0
    assert plan_frame_times("video", None) == [0.0]


def test_aggregate_takes_max_and_flags_implied_family():
    frames = [
        {"scores": {"nudity": 0.2, "nudity/bareButtocks": 0.7, "violence": 0.4}},
        {"scores": {"nudity": 0.5, "nudity/bareButtocks": 0.1, "violence": 0.6}},
    ]
    scores, flagged = aggregate(frames, {"violence": 0.65, "*": 0.6})
    assert scores == {"nudity": 0.5, "nudity/bareButtocks": 0.7, "violence": 0.6}
    assert flagged == ["nudity", "nudity/bareButtocks"]


def test_load_results_last_line_wins_and_skips_truncated(tmp_path):
    path = tmp_path / "m.jsonl"
    path.write_text(
        json.dumps({"id": "a", "status": "error"}) + "\n"
        + json.dumps({"id": "a", "status": "ok"}) + "\n"
        + '{"id": "b", "sta',
        encoding="utf-8",
    )
    assert load_results(path) == {"a": {"id": "a", "status": "ok"}}


def test_summary_metrics_on_synthetic_results(tmp_path):
    items = [
        {"id": "1", "path": "video/x/a.mp4", "expected": ["violence", "violence/punching"],
         "scored": True},
        {"id": "2", "path": "video/x/b.mp4", "expected": ["violence"], "scored": True},
        {"id": "3", "path": "video/x/c.mp4", "expected": [], "scored": True},
        {"id": "4", "path": "video/y/d.mp4", "expected": [], "scored": True},
        {"id": "5", "path": "video/y/e.mp4", "expected": ["violence"], "scored": False},
        {"id": "6", "path": "video/y/f.mp4", "expected": [], "scored": True},
    ]
    lines = [
        {"id": "1", "status": "ok", "elapsed_ms": 40, "frames": [{}, {}],
         "flagged": ["violence", "violence/punching"]},
        {"id": "2", "status": "ok", "elapsed_ms": 20, "frames": [{}], "flagged": []},
        {"id": "3", "status": "ok", "elapsed_ms": 30, "frames": [{}], "flagged": ["violence"]},
        {"id": "4", "status": "ok", "elapsed_ms": 10, "frames": [{}], "flagged": ["nudity"]},
        {"id": "5", "status": "ok", "elapsed_ms": 10, "frames": [{}], "flagged": ["violence"]},
        {"id": "6", "status": "error", "elapsed_ms": 1, "frames": [], "flagged": []},
    ]
    path = tmp_path / "m.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    meta = {"supports": ["violence", "violence/punching"], "peak_rss_mb": 12.5}

    summary = summarize_model(items, load_results(path), meta)

    violence = summary["families"]["violence"]
    assert {k: violence[k] for k in ("tp", "fp", "fn", "tn", "n")} == {
        "tp": 1, "fp": 1, "fn": 1, "tn": 1, "n": 4,
    }
    assert (violence["precision"], violence["recall"], violence["f1"]) == (0.5, 0.5, 0.5)
    assert set(summary["families"]) == {"violence"}
    assert summary["tokens"]["violence/punching"]["tp"] == 1
    assert summary["tokens"]["violence/punching"]["fn"] == 0
    assert (summary["items_scored"], summary["items_error"]) == (4, 1)
    assert summary["mean_ms_per_frame"] == 20.0
    assert summary["by_folder"]["video/x"]["violence"]["n"] == 3


def test_viewer_install_copies_tree_recursively(tmp_path):
    source = tmp_path / "viewer"
    (source / "js" / "lib").mkdir(parents=True)
    (source / "index.html").write_text("x")
    (source / "js" / "lib" / "a.js").write_text("y")
    (source / ".DS_Store").write_text("z")
    layout = Layout(tmp_path / "data")

    copied = install_viewer(layout, source)

    assert copied == ["index.html", "js/lib/a.js"]
    assert (layout.results / "js" / "lib" / "a.js").read_text() == "y"
    assert not (layout.results / ".DS_Store").exists()


def _tag_map():
    from scanner.evaluation.folder_eval.film_labels import TagMap

    return TagMap.load()


def test_film_window_labels_padding_and_ambiguity():
    from scanner.evaluation.folder_eval.film_labels import Tag

    tag_map = _tag_map()
    tags = [
        Tag("violence/gore", 22.0, 22.0),
        Tag("immodesty/nudity_implied", 45.0, 46.0),
        Tag("language/profanity", 51.0, 51.0),
        Tag("sexual/sex_with_nudity", 70.0, 75.0),
    ]
    # Point event at 22 s padded to [19, 25] touches both 10-20 and 20-30 windows.
    first = tag_map.label_window(10, 20, tags)
    assert first.expected == ["fear", "fear/hemophobia", "violence"] and first.scored
    assert tag_map.label_window(30, 40, tags).expected == []
    ambiguous = tag_map.label_window(40, 50, tags)
    assert ambiguous.expected == [] and not ambiguous.scored
    assert ambiguous.source_tags == ["immodesty/nudity_implied", "language/profanity"]
    sex = tag_map.label_window(70, 80, tags)
    assert sex.expected == ["nudity", "sex", "sex/coitus"] and sex.scored


def test_film_window_items_and_imdb_resolution():
    from scanner.evaluation.folder_eval.film_labels import Tag
    from scanner.evaluation.folder_eval.films import (
        film_folder,
        resolve_imdb_id,
        window_item_id,
        window_items,
    )

    assert film_folder("John Wick: Chapter 4", 2023) == "John Wick - Chapter 4 (2023)"
    movies = [
        {"imdb_id": "tt1", "name": "Suspiria", "title_main": "Suspiria", "year": "1977"},
        {"imdb_id": "tt2", "name": "Suspiria", "title_main": "Suspiria", "year": "2018"},
    ]
    assert resolve_imdb_id({"title": "Suspiria", "year": 2018}, movies) == "tt2"
    with pytest.raises(ValueError):
        resolve_imdb_id({"title": "Suspiria", "year": 2018, "imdb_id": "tt1"}, movies)

    items = window_items(
        "films/F (2000)/f.mkv", "films/F (2000)", "tt2", 24.0,
        [Tag("violence/graphic", 14.0, 15.0)], _tag_map(), hdr=True,
    )
    assert [item["window"] for item in items] == [[0.0, 10.0], [10.0, 20.0], [20.0, 24.0]]
    assert [[f["t"] for f in item["frames"]] for item in items] == [
        [2.5, 7.5], [12.5, 17.5], [22.5],
    ]
    assert items[1]["id"] == window_item_id("films/F (2000)/f.mkv", 10.0)
    assert items[1]["expected"] == ["fear", "fear/hemophobia", "violence"] and items[1]["hdr"]
    assert items[0]["expected"] == [] and items[0]["source_tags"] == []


def test_summary_separates_film_windows():
    items = [
        {"id": "c", "path": "video/x/a.mp4", "expected": ["violence"], "scored": True},
        {"id": "w1", "path": "films/F/f.mkv", "folder": "films/F", "imdb_id": "tt9",
         "window": [0, 10], "expected": ["violence"], "scored": True},
        {"id": "w2", "path": "films/F/f.mkv", "folder": "films/F", "imdb_id": "tt9",
         "window": [10, 20], "expected": [], "scored": True},
    ]
    results = {
        identifier: {"id": identifier, "status": "ok", "frames": [{}], "flagged": ["violence"]}
        for identifier in ("c", "w1", "w2")
    }
    summary = summarize_model(items, results, {"supports": ["violence"]})
    assert summary["families"]["violence"]["n"] == 1
    film = summary["films"]["films/F"]
    assert film["imdb_id"] == "tt9" and film["windows"] == 2
    assert {k: film["families"]["violence"][k] for k in ("tp", "fp")} == {"tp": 1, "fp": 1}
    assert summary["films"]["all"]["windows"] == 2
    assert "films/F" in summary["by_folder"]


def test_append_line_is_a_single_write(tmp_path):
    from scanner.evaluation.folder_eval.results import append_line

    path = tmp_path / "m.jsonl"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        append_line(descriptor, {"id": "a", "big": "x" * 100_000})
        append_line(descriptor, {"id": "b"})
    finally:
        os.close(descriptor)
    assert set(load_results(path)) == {"a", "b"}


def test_decision_sample_is_stratified_and_deterministic():
    from scanner.evaluation.folder_eval.subsets import build_decision_sample, middle_frame

    items = [clip(f"a{i}", "video/x", ["violence"]) for i in range(50)]
    items += [clip(f"b{i}", "video/x", []) for i in range(5)]
    items += [clip(f"c{i}", "video/y", [], scored=False) for i in range(3)]
    items += [clip("img", "video/z", [], kind="image")]
    for start in range(0, 70, 10):
        items.append({
            "id": f"w{start}", "path": "films/F/f.mkv", "folder": "films/F",
            "window": [start, start + 10], "expected": [], "scored": start != 30,
            "frames": [{"n": 0, "t": start + 2.5}, {"n": 1, "t": start + 7.5}],
        })

    sample = build_decision_sample(items, cap=12)

    assert sample == build_decision_sample(items, cap=12)
    ids = [entry["id"] for entry in sample]
    # Windows 0, 30, 60 are every third; 30 is unscored and dropped.
    assert [i for i in ids if i.startswith("w")] == ["w0", "w60"]
    assert sum(i.startswith("b") for i in ids) == 5 and sum(i.startswith("a") for i in ids) == 7
    assert not any(i.startswith("c") for i in ids) and "img" in ids
    frames = {entry["id"]: entry["frames"] for entry in sample}
    assert frames["w0"] == [0] and frames["img"] == [0]
    # 8 s clip frames at 1, 3, 5, 7 s: 3 and 5 tie for 4 s; the tie goes to n = len // 2.
    assert frames["b0"] == [middle_frame(items[50])] == [2]


def test_restricted_results_use_only_subset_frames_and_missing_keys_are_zero():
    from scanner.evaluation.folder_eval.subsets import restrict_results

    results = {
        "a": {"id": "a", "status": "ok", "frames": [
            {"n": 0, "scores": {"violence": 0.9}},
            {"n": 2, "scores": {"violence": 0.1, "violence/murder": 0.2}, "asked": ["violence"]},
        ]},
        "b": {"id": "b", "status": "ok", "frames": [{"n": 0, "scores": {"violence": 0.9}}]},
    }
    restricted = restrict_results(results, {"a": [2], "b": [1]}, {"*": 0.5})
    assert set(restricted) == {"a"}
    assert restricted["a"]["flagged"] == [] and restricted["a"]["scores"]["violence"] == 0.1


def test_token_only_model_gets_token_rows_for_clips_films_and_subset():
    from scanner.evaluation.folder_eval.summary import summarize_subset

    key = "fear/hemophobia"
    items = [
        {"id": "c", "path": "video/x/c.mp4", "folder": "video/x",
         "expected": ["fear", key], "scored": True},
        {"id": "w1", "path": "films/F/f.mkv", "folder": "films/F", "window": [0, 10],
         "expected": ["fear", key, "violence"], "scored": True},
        {"id": "w2", "path": "films/F/f.mkv", "folder": "films/F", "window": [10, 20],
         "expected": [], "scored": True},
    ]
    results = {
        identifier: {"id": identifier, "status": "ok", "flagged": flagged,
                     "frames": [{"n": 0, "scores": {key: score}}]}
        for identifier, flagged, score in (
            ("c", [key, "fear"], 0.9), ("w1", [], 0.001), ("w2", [key, "fear"], 0.5),
        )
    }
    meta = {"supports": [key], "thresholds": {key: 0.004}}

    summary = summarize_model(items, results, meta)
    assert summary["families"] == {} and summary["tokens"][key]["tp"] == 1
    film = summary["films"]["films/F"]
    assert film["families"] == {}
    assert {k: film["tokens"][key][k] for k in ("tp", "fp", "fn")} == {"tp": 0, "fp": 1, "fn": 1}
    assert key in summary["by_folder"]["films/F"]
    subset = summarize_subset(items, results, meta, {"w1": [0], "w2": [0]})
    assert subset["films"]["all"]["tokens"][key]["fp"] == 1


def test_graphic_violence_implies_hemophobia():
    from scanner.evaluation.folder_eval.film_labels import Tag, TagMap

    label = TagMap.load().label_window(0, 10, [Tag("violence/graphic", 4.0, 5.0)])
    assert label.expected == ["fear", "fear/hemophobia", "violence"]


def test_siglip2_gore_scores_max_positive_prompt(monkeypatch):
    from scanner.evaluation.folder_eval import siglip2_gore

    backend = object.__new__(siglip2_gore.Siglip2GoreBackend)
    probabilities = dict.fromkeys(siglip2_gore.POSITIVE_PROMPTS, 0.001)
    probabilities["graphic gore"] = 0.03
    probabilities.update(dict.fromkeys(siglip2_gore.NEUTRAL_PROMPTS, 0.9))
    monkeypatch.setattr(backend, "probabilities", lambda paths: [probabilities for _ in paths])

    (output,) = backend.score_frames([Path("f.jpg")])
    assert output["scores"] == {"fear/hemophobia": 0.03}
    assert "graphic gore 0.0300" in output["labels"]
