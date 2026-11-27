"""Frame extraction: HDR tone-mapping paths, film-window fallbacks and `--limit`."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from scanner.evaluation.folder_eval import frames as frames_module
from scanner.evaluation.folder_eval import tonemap
from scanner.evaluation.folder_eval.paths import Layout
from tests.folder_eval_support import clip

FILTER_LISTING = """Filters:
  T.. = Timeline support
  ------
 TS aap               AA->A      Apply Affine Projection algorithm.
 .. scale_vt          V->V       Scale Videotoolbox frames
 .S tonemap           V->V       Conversion to/from different dynamic ranges.
 .. zscale            V->V       Apply resizing, colorspace and bit depth conversion.
"""


@pytest.fixture
def filters(monkeypatch):
    """Set the filters `ffmpeg -filters` reports."""

    def use(*names):
        monkeypatch.setattr(tonemap, "ffmpeg_filters", lambda: frozenset(names))

    return use


def test_ffmpeg_filters_parses_the_listing_once(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=FILTER_LISTING)

    tonemap.ffmpeg_filters.cache_clear()
    monkeypatch.setattr(tonemap.subprocess, "run", run)
    try:
        assert tonemap.ffmpeg_filters() == {"aap", "scale_vt", "tonemap", "zscale"}
        tonemap.ffmpeg_filters()
    finally:
        tonemap.ffmpeg_filters.cache_clear()
    assert calls == [["ffmpeg", "-hide_banner", "-filters"]]


@pytest.mark.parametrize(
    ("platform", "names", "expected"),
    [
        ("darwin", ("scale_vt", "zscale", "tonemap"), tonemap.VIDEOTOOLBOX),
        ("darwin", ("zscale", "tonemap"), tonemap.ZSCALE),
        ("linux", ("scale_vt", "zscale", "tonemap", "libplacebo"), tonemap.ZSCALE),
        ("linux", ("tonemap", "libplacebo"), tonemap.LIBPLACEBO),
        ("linux", ("tonemap",), None),
        ("win32", (), None),
    ],
)
def test_hdr_path_depends_on_platform_and_filters(filters, platform, names, expected):
    filters(*names)
    assert tonemap.select_hdr_path(platform) == expected


def _item(**extra):
    item = clip("a", "films/F", ["violence"], frames=1)
    item.update(extra)
    return item


@pytest.fixture
def commands(tmp_path, monkeypatch):
    """Record ffmpeg commands; every attempt fails."""
    recorded = []
    (tmp_path / "films/F").mkdir(parents=True)
    (tmp_path / "films/F/a.mp4").write_bytes(b"x")
    monkeypatch.setattr(
        frames_module.subprocess, "run", lambda command, **_k: recorded.append(command)
    )
    return recorded


def test_hdr_item_without_a_tone_mapping_path_fails_without_decoding(tmp_path, commands):
    _id, written, errors = frames_module.extract_item(str(tmp_path), _item(hdr=True), None)
    assert (written, errors, commands) == (0, [tonemap.NO_HDR_PATH], [])


def test_hdr_item_uses_software_tone_mapping_off_macos(tmp_path, commands):
    frames_module.extract_item(str(tmp_path), _item(hdr=True), tonemap.ZSCALE)
    assert commands and all("-hwaccel" not in command for command in commands)
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "zscale=t=linear" in video_filter and "tonemap=" in video_filter


def test_film_window_never_falls_back_to_the_opening_frame(tmp_path, commands):
    item = _item(window=[600.0, 610.0])
    item["frames"][0]["t"] = 602.5
    _id, written, errors = frames_module.extract_item(str(tmp_path), item)
    seeks = {float(command[command.index("-ss") + 1]) for command in commands}
    assert written == 0 and errors and 0.0 not in seeks


def test_clip_still_falls_back_to_the_first_frame(tmp_path, commands):
    frames_module.extract_item(str(tmp_path), _item())
    assert "0.000" in [command[command.index("-ss") + 1] for command in commands]


def test_frames_limit_applies_to_pending_items(tmp_path, monkeypatch):
    layout = Layout(tmp_path)
    items = [clip(identifier, "video/x", [], frames=1) for identifier in "abcd"]
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    extracted = []

    def fake_extract(_root, item, _hdr_path):
        target = layout.results / item["frames"][0]["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        extracted.append(item["id"])
        return item["id"], 1, []

    monkeypatch.setattr(frames_module, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(frames_module, "extract_item", fake_extract)
    frames_module.extract_frames(layout, limit=2, workers=1)
    frames_module.extract_frames(layout, limit=2, workers=1)
    assert sorted(extracted) == ["a", "b", "c", "d"]
