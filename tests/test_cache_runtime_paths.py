"""Focused NATIVE-03 coverage for regenerable cache and scan-workspace paths."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from scanner.runtime.runtime_paths import RuntimePaths, resolve_runtime_paths
from scanner.runtime.runtime_support import (
    configure_model_cache_environment,
    resolve_ffmpeg,
    resolve_ffprobe,
)
from scanner.runtime.runtime_workspaces import (
    cleanup_stale_scan_workspaces,
    create_scan_workspace,
    remove_scan_workspace,
)
from scanner.settings.config import Settings


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        data_dir=tmp_path / "durable data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "regenerable cache",
        temp_dir=tmp_path / "runtime temporary",
        resources_dir=tmp_path / "resources",
    )


def test_model_cache_is_platform_derived_and_data_override_does_not_move_it():
    paths = resolve_runtime_paths(
        platform="linux",
        data_dir="/portable/durable data",
        environment={"XDG_CACHE_HOME": "/var/cache root"},
        home_dir="/home/editarr",
        temporary_dir="/tmp/editarr",
    )

    assert paths.cache_dir == Path("/var/cache root/editarr")
    assert paths.model_cache == Path("/var/cache root/editarr/models")
    assert paths.data_dir == Path("/portable/durable data")


def test_deleting_cache_never_deletes_durable_replay_or_configuration(tmp_path):
    paths = _paths(tmp_path)
    settings = Settings(
        data_dir=paths.data_dir,
        cache_dir=paths.cache_dir,
        model_cache=paths.model_cache,
    )
    replay = settings.qwen_replays_dir / "scan-1" / "segment.json"
    replay.parent.mkdir(parents=True)
    replay.write_text('{"durable": true}', encoding="utf-8")
    deployment = settings.data_dir / "deployment.json"
    deployment.write_text('{"port": 9123}', encoding="utf-8")
    cached_model = settings.model_cache / "qwen" / "weights.safetensors"
    cached_model.parent.mkdir(parents=True)
    cached_model.write_bytes(b"regenerable")

    shutil.rmtree(settings.cache_dir)

    assert replay.read_text(encoding="utf-8") == '{"durable": true}'
    assert deployment.is_file()
    assert not cached_model.exists()


def test_model_cache_environment_is_early_and_respects_explicit_operator_values(
    monkeypatch, tmp_path
):
    paths = _paths(tmp_path)
    names = (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "CT2_CACHE_DIR",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    configured = configure_model_cache_environment(paths)

    assert configured["HF_HOME"] == paths.model_cache / "huggingface"
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(paths.model_cache / "huggingface" / "hub")
    assert os.environ["TRANSFORMERS_CACHE"] == str(
        paths.model_cache / "huggingface" / "transformers"
    )
    monkeypatch.setenv("HF_HOME", "/operator/hf-cache")
    assert configure_model_cache_environment(paths)["HF_HOME"] == Path("/operator/hf-cache")


def test_scan_workspaces_are_unique_and_stale_cleanup_skips_live_or_unowned_paths(tmp_path):
    root = tmp_path / "runtime"
    first = create_scan_workspace(root, "scan / same")
    second = create_scan_workspace(root, "scan / same")
    assert first != second
    assert first.parent == second.parent == root

    stale = root / "scan-stale"
    stale.mkdir()
    (stale / ".editarr-scan-workspace.json").write_text(
        json.dumps({"scan_id": "old", "pid": 999_999_999, "created_at": 1.0}),
        encoding="utf-8",
    )
    live = root / "scan-live"
    live.mkdir()
    (live / ".editarr-scan-workspace.json").write_text(
        json.dumps({"scan_id": "live", "pid": os.getpid(), "created_at": 1.0}),
        encoding="utf-8",
    )
    unowned = root / "scan-unowned"
    unowned.mkdir()

    removed = cleanup_stale_scan_workspaces(root, now=100_000, stale_after_seconds=10)

    assert removed == [stale]
    assert not stale.exists()
    assert first.exists() and second.exists() and live.exists() and unowned.exists()
    remove_scan_workspace(first)
    remove_scan_workspace(second)


def test_helper_resolver_uses_immutable_resources_independent_of_cwd(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    helpers = Path(paths.resources_dir) / "helpers"
    helpers.mkdir(parents=True)
    ffmpeg = helpers / "ffmpeg"
    ffprobe = helpers / "ffprobe"
    ffmpeg.write_text("helper", encoding="utf-8")
    ffprobe.write_text("helper", encoding="utf-8")
    arbitrary_cwd = tmp_path / "arbitrary cwd"
    arbitrary_cwd.mkdir()
    monkeypatch.chdir(arbitrary_cwd)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)

    assert resolve_ffmpeg(paths) == str(ffmpeg)
    assert resolve_ffprobe(paths) == str(ffprobe)
    assert not (arbitrary_cwd / "data").exists()
