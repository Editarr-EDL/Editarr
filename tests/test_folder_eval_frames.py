"""Frame extraction: HDR tone-mapping paths, film-window fallbacks and `--limit`."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def _failed(command, **_kwargs):
    return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"decode error")


@pytest.fixture
def commands(tmp_path, monkeypatch):
    """Record ffmpeg commands; every attempt fails with a non-zero exit."""
    recorded = []
    (tmp_path / "films/F").mkdir(parents=True)
    (tmp_path / "films/F/a.mp4").write_bytes(b"x")

    def run(command, **_kwargs):
        if command[0] == "ffmpeg":
            recorded.append(command)
        return _failed(command)

    monkeypatch.setattr(frames_module.subprocess, "run", run)
    return recorded


def test_hdr_item_without_a_tone_mapping_path_fails_without_decoding(tmp_path, commands):
    item = _item(hdr=True)
    _id, written, errors, _times = frames_module.extract_item(str(tmp_path), item, None)
    assert (written, errors, commands) == (0, [tonemap.NO_HDR_PATH], [])


def test_hdr_item_uses_software_tone_mapping_off_macos(tmp_path, commands):
    frames_module.extract_item(str(tmp_path), _item(hdr=True), tonemap.ZSCALE)
    assert commands and all("-hwaccel" not in command for command in commands)
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "zscale=t=linear" in video_filter and "tonemap=" in video_filter


def test_film_window_never_falls_back_to_the_opening_frame(tmp_path, commands):
    item = _item(window=[600.0, 610.0])
    item["frames"][0]["t"] = 602.5
    _id, written, errors, _times = frames_module.extract_item(str(tmp_path), item)
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
        return item["id"], 1, [], {}

    monkeypatch.setattr(frames_module, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(frames_module, "extract_item", fake_extract)
    frames_module.extract_frames(layout, limit=2, workers=1)
    frames_module.extract_frames(layout, limit=2, workers=1)
    assert sorted(extracted) == ["a", "b", "c", "d"]


def test_stale_or_failed_partial_frames_are_never_promoted(tmp_path, monkeypatch):
    (tmp_path / "video/x").mkdir(parents=True)
    (tmp_path / "video/x/a.mp4").write_bytes(b"x")
    item = clip("a", "video/x", [], frames=1)
    partial = tmp_path / "_results/frames/a/0.part.jpg"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"stale")

    def failing_after_writing(command, **_kwargs):
        if command[-1].endswith("0.part.jpg") and "-ss" in command:
            assert not partial.exists(), "stale partial was not removed before the attempt"
            partial.write_bytes(b"half")
        return _failed(command)

    monkeypatch.setattr(frames_module.subprocess, "run", failing_after_writing)
    _id, written, errors, _times = frames_module.extract_item(str(tmp_path), item)
    assert written == 0 and errors
    assert not (tmp_path / "_results/frames/a/0.jpg").exists()


def _decoder(tmp_path, monkeypatch, landing):
    """Fake ffprobe (start time 1.4 s) and ffmpeg that writes the frame and reports the
    decoded pts `landing(seek, not_before)` in source time; returns the ffmpeg commands."""
    commands = []
    (tmp_path / "films/F").mkdir(parents=True, exist_ok=True)
    (tmp_path / "films/F/a.ts").write_bytes(b"x")

    def run(command, **_kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="1.400000\n", stderr="")
        commands.append(command)
        seek = float(command[command.index("-ss") + 1])
        video_filter = command[command.index("-vf") + 1]
        not_before = float(video_filter.split("gte(t\\,")[1].split(")")[0])
        Path(command[-1]).write_bytes(b"jpeg")
        stderr = f"[Parsed_showinfo_2] n:0 pts:1 pts_time:{landing(seek, not_before)}".encode()
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=stderr)

    monkeypatch.setattr(frames_module.subprocess, "run", run)
    return commands


def _window_item():
    return {"id": "w", "path": "films/F/a.ts", "window": [600.0, 610.0],
            "frames": [{"n": 0, "t": 602.5, "file": "frames/w/0.jpg"}]}


def test_film_window_seek_overshoot_retries_from_earlier_and_records_the_time(
    tmp_path, monkeypatch
):
    # Seeking at the wanted time lands a keyframe interval late; an earlier seek decodes
    # forward to the first frame at or after the wanted time.
    commands = _decoder(
        tmp_path, monkeypatch,
        lambda seek, not_before: not_before + 7.5 if seek > 600 else not_before + 0.02,
    )
    _id, written, errors, times = frames_module.extract_item(str(tmp_path), _window_item())
    assert (written, errors, times) == (1, [], {"0": 602.52})
    assert [float(c[c.index("-ss") + 1]) for c in commands] == [602.5, 592.5]
    assert all("-copyts" in c for c in commands)


def test_film_window_frame_outside_its_window_fails(tmp_path, monkeypatch):
    _decoder(tmp_path, monkeypatch, lambda _seek, not_before: not_before + 9.0)
    _id, written, errors, times = frames_module.extract_item(str(tmp_path), _window_item())
    assert written == 0 and times == {}
    assert "outside window 600.0-610.0s" in errors[0]
    assert not (tmp_path / "_results/frames/w/0.jpg").exists()


def test_extract_frames_records_decoded_times(tmp_path, monkeypatch):
    layout = Layout(tmp_path)
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": [_window_item()]}), encoding="utf-8")
    _decoder(tmp_path, monkeypatch, lambda _seek, not_before: not_before + 0.04)
    monkeypatch.setattr(frames_module, "ProcessPoolExecutor", ThreadPoolExecutor)
    assert frames_module.extract_frames(layout, workers=1) == {}
    recorded = json.loads((layout.results / frames_module.FRAME_TIMES).read_text())
    assert recorded == {"w": {"0": 602.54}}
