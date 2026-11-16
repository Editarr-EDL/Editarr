"""Payload accounting distinguishes copies, aliases and compressed artifacts."""

from __future__ import annotations

import importlib
import json
import os

import pytest

from native_build.artifact_size import (
    SIZE_ACCOUNTING,
    audit_size,
    size_breakdown,
    tree_size,
    write_sized_inventory,
)


def test_size_ignores_file_directory_and_broken_symlinks(tmp_path):
    payload = tmp_path / "_internal" / "torch"
    payload.mkdir(parents=True)
    (payload / "library").write_bytes(b"0123456789")
    external = tmp_path.parent / "external-library"
    external.write_bytes(b"external")
    (tmp_path / "alias").symlink_to(payload / "library")
    (tmp_path / "external-alias").symlink_to(external)
    (tmp_path / "directory-alias").symlink_to(payload, target_is_directory=True)
    (tmp_path / "broken").symlink_to(tmp_path / "absent")

    assert tree_size(tmp_path) == 10
    assert size_breakdown(tmp_path) == {"_internal/torch": 10}
    report = audit_size(tmp_path)
    assert report["file_count"] == 1
    assert report["symlink_count"] == 4
    assert report["unique_file_bytes"] == 10


@pytest.mark.parametrize(
    "module", ["build_release", "package_macos", "package_linux", "package_windows"]
)
def test_all_packagers_use_link_aware_accounting(tmp_path, module):
    (tmp_path / "library").write_bytes(b"ten bytes!")
    (tmp_path / "alias").symlink_to("library")
    package = importlib.import_module(f"scripts.{module}")
    assert package._tree_size(tmp_path) == 10


def test_audit_detects_copies_but_does_not_count_links_as_redundant(tmp_path):
    payload = b"a" * (1024 * 1024)
    original = tmp_path / "original"
    original.write_bytes(payload)
    (tmp_path / "copy").write_bytes(payload)
    (tmp_path / "different").write_bytes(b"b" * len(payload))
    (tmp_path / "symbolic").symlink_to(original)
    os.link(original, tmp_path / "hard-link")

    report = audit_size(tmp_path, duplicates=True)

    assert report["regular_file_bytes"] == len(payload) * 4
    assert report["unique_file_bytes"] == len(payload) * 3
    assert report["duplicate_bytes"] == len(payload)
    assert len(report["duplicate_files"]) == 1
    matches = report["duplicate_files"][0]["paths"]
    assert "copy" in matches
    assert {"original", "hard-link"}.intersection(matches)
    assert "symbolic" not in matches


def test_inventory_includes_its_own_final_bytes_and_survives_refresh(tmp_path):
    (tmp_path / "Editarr").write_bytes(b"runtime")
    (tmp_path / "alias").symlink_to("Editarr")
    inventory = {"format": 1, "runtime_size_bytes": 0}
    path = tmp_path / "editarr-runtime-inventory.json"

    write_sized_inventory(path, inventory)
    saved = path.read_bytes()

    assert json.loads(saved)["size_accounting"] == SIZE_ACCOUNTING
    assert inventory["runtime_size_bytes"] == len(saved) + len(b"runtime")
    assert sum(inventory["size_breakdown_bytes"].values()) == inventory["runtime_size_bytes"]
    write_sized_inventory(path, inventory)
    assert path.read_bytes() == saved

    (tmp_path / "Editarr").write_bytes(b"larger refreshed runtime")
    windows = importlib.import_module("scripts.package_windows")
    windows._refresh_runtime_size(tmp_path, inventory)
    assert json.loads(path.read_text())["runtime_size_bytes"] == tree_size(tmp_path)


def test_bundle_breakdown_identifies_embedded_runtime_packages(tmp_path):
    package = tmp_path / "Contents/Resources/runtime/Editarr/_internal/torch"
    package.mkdir(parents=True)
    (package / "library").write_bytes(b"torch")
    (tmp_path / "Contents/launcher").write_bytes(b"launcher")

    assert size_breakdown(tmp_path) == {
        "Contents": len(b"launcher"),
        "Contents/Resources/runtime/Editarr/_internal/torch": len(b"torch"),
    }
