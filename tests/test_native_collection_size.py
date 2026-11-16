"""Keep runtime engines while excluding duplicate helpers and build resources."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PyInstaller.utils.hooks import collect_data_files

from native_build import pyinstaller_collect as collection

HOOKS = Path(__file__).resolve().parents[1] / "native_build" / "hooks"


def test_collector_filters_development_imports_before_discovery(monkeypatch):
    calls = []

    def collect(package, *, filter_submodules, exclude_datas):
        calls.append((package, exclude_datas))
        imports = [package, f"{package}.models", f"{package}.tests", f"{package}.tests.helper"]
        return [], [], [name for name in imports if filter_submodules(name)]

    monkeypatch.setattr(collection, "collect_all", collect)
    _datas, _binaries, hidden = collection.collected_packages("macos-arm64")
    assert "torch.models" in hidden
    assert "mlx_vlm.models" in hidden
    assert not any("tests" in name.split(".") for name in hidden)
    assert dict(calls)["torch"] == ["include/**", "bin/protoc*"]
    assert dict(calls)["imageio_ffmpeg"] == ["binaries/ffmpeg-*"]


@pytest.mark.parametrize("package", ["torch", "imageio_ffmpeg"])
def test_real_package_data_exclusions_keep_sources_and_remove_targeted_payloads(package):
    data = collect_data_files(
        package, include_py_files=True, excludes=collection._data_exclusions(package)
    )
    paths = [Path(source) for source, _destination in data]
    assert any(path.suffix == ".py" for path in paths)
    if package == "torch":
        assert not any("include" in path.parts or path.name.startswith("protoc") for path in paths)
    else:
        assert not any(path.name.startswith("ffmpeg-") for path in paths)


@pytest.mark.parametrize(
    "platform,name", [("darwin", "ffmpeg"), ("linux", "ffmpeg"), ("win32", "ffmpeg.exe")]
)
def test_runtime_hook_selects_single_helper_for_imageio(tmp_path, monkeypatch, platform, name):
    helper = tmp_path / "resources with spaces" / "helpers" / name
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"helper")
    monkeypatch.setattr(sys, "_MEIPASS", str(helper.parent.parent), raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "")

    runpy.run_path(str(HOOKS / "pyi_rth_editarr_ffmpeg.py"))

    import imageio_ffmpeg

    assert os.environ["IMAGEIO_FFMPEG_EXE"] == str(helper)
    assert imageio_ffmpeg.get_ffmpeg_exe() == str(helper)


def test_runtime_hook_preserves_explicit_operator_override(monkeypatch):
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "/operator/ffmpeg")
    runpy.run_path(str(HOOKS / "pyi_rth_editarr_ffmpeg.py"))
    assert os.environ["IMAGEIO_FFMPEG_EXE"] == "/operator/ffmpeg"


def test_runtime_hook_rejects_missing_helper_without_system_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "")
    with pytest.raises(RuntimeError, match="FFmpeg helper is missing"):
        runpy.run_path(str(HOOKS / "pyi_rth_editarr_ffmpeg.py"))


def test_custom_imageio_hook_keeps_resource_namespace_without_binary_data():
    hook = runpy.run_path(str(HOOKS / "hook-imageio_ffmpeg.py"))
    assert hook["datas"] == []
    assert "imageio_ffmpeg.binaries" in hook["hiddenimports"]


def test_imageio_frame_count_uses_relocated_helper_without_discovery(tmp_path, monkeypatch):
    import imageio_ffmpeg

    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "")
    binary = Path(imageio_ffmpeg.get_ffmpeg_exe())
    helper = tmp_path / "copied runtime" / "helpers" / binary.name
    helper.parent.mkdir(parents=True)
    helper = helper.with_name("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    shutil.copy2(binary, helper)
    video = tmp_path / "generated sample.mp4"
    subprocess.run(
        [
            str(helper),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=32x32:rate=6:duration=1",
            "-c:v",
            "mpeg4",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    monkeypatch.setattr(sys, "_MEIPASS", str(helper.parent.parent), raising=False)
    runpy.run_path(str(HOOKS / "pyi_rth_editarr_ffmpeg.py"))
    monkeypatch.setattr(
        imageio_ffmpeg._utils,
        "_get_ffmpeg_exe",
        lambda: pytest.fail("imageio tried to discover a second/system FFmpeg"),
    )

    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(video))

    assert frames == 6
    assert seconds == pytest.approx(1.0)
