"""Linux AppImage packaging policy tests that do not claim native-host evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import tomllib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from packaging.markers import Marker
from packaging.tags import cpython_tags
from packaging.utils import parse_wheel_filename


def test_linux_lock_selects_installable_cpu_torch_and_torchvision():
    lock = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    environment = {
        "sys_platform": "linux",
        "platform_machine": "x86_64",
        "python_full_version": "3.11.13",
        "python_version": "3.11",
    }
    compatible = set(cpython_tags(
        python_version=(3, 11),
        platforms=[f"manylinux_2_{minor}_x86_64" for minor in range(28, 4, -1)],
    ))
    project = next(package for package in lock["package"] if package["name"] == "editarr")
    selected = {}
    for dependency in project["dependencies"]:
        if dependency["name"] not in {"torch", "torchvision"}:
            continue
        if not Marker(dependency.get("marker", "python_version >= '3.11'")).evaluate(
            environment
        ):
            continue
        package = next(package for package in lock["package"] if (
            package["name"] == dependency["name"]
            and package["version"] == dependency["version"]
            and package["source"] == dependency["source"]
        ))
        assert package["version"].endswith("+cpu")
        assert any(
            parse_wheel_filename(unquote(wheel["url"].rsplit("/", 1)[-1]))[3] & compatible
            for wheel in package["wheels"]
        ), f"{package['name']} has no CPython 3.11/glibc 2.28 x86_64 wheel"
        selected[package["name"]] = package
    assert selected.keys() == {"torch", "torchvision"}
    torch_dependency = next(
        item for item in selected["torchvision"]["dependencies"] if item["name"] == "torch"
    )
    assert torch_dependency["version"] == selected["torch"]["version"]


def _load_package_linux():
    path = Path("scripts/package_linux.py")
    specification = importlib.util.spec_from_file_location("package_linux", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _elf_x86_64(path: Path) -> None:
    header = bytearray(20)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, 62)
    path.write_bytes(header)
    path.chmod(0o755)


def _runtime(tmp_path: Path, package) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _elf_x86_64(runtime / "Editarr")
    (runtime / "editarr-runtime-inventory.json").write_text(
        json.dumps(
            {
                "format": 1,
                "target": package.TARGET,
                "version": package._project_version(),
                "source_revision": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    return runtime


def test_linux_package_rejects_non_linux_and_non_x86_64_hosts(monkeypatch):
    package = _load_package_linux()
    monkeypatch.setattr(package.sys, "platform", "darwin")
    with pytest.raises(package.PackageError, match="natively on Linux"):
        package._require_linux_x86_64()

    monkeypatch.setattr(package.sys, "platform", "linux")
    monkeypatch.setattr(package.platform, "machine", lambda: "aarch64")
    with pytest.raises(package.PackageError, match="unsupported Linux packaging architecture"):
        package._require_linux_x86_64()


def test_appstream_validation_supports_baseline_and_newer_distro_tools(
    monkeypatch, tmp_path
):
    package = _load_package_linux()
    metadata = tmp_path / "org.editarr.Editarr.metainfo.xml"
    monkeypatch.setattr(
        package.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command == "appstreamcli" else None,
    )
    assert package._appstream_validation_command(metadata) == [
        "/usr/bin/appstreamcli",
        "validate",
        "--no-net",
        str(metadata),
    ]

    monkeypatch.setattr(
        package.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command == "appstream-util" else None,
    )
    assert package._appstream_validation_command(metadata) == [
        "/usr/bin/appstream-util",
        "validate-relax",
        str(metadata),
    ]

def test_runtime_identity_requires_inventory_version_target_and_x86_64(tmp_path):
    package = _load_package_linux()
    runtime = _runtime(tmp_path, package)

    inventory = package._read_runtime_inventory(runtime)
    assert inventory["target"] == "linux-x86_64"

    inventory["target"] = "windows-x64"
    (runtime / "editarr-runtime-inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )
    with pytest.raises(package.PackageError, match="linux-x86_64"):
        package._read_runtime_inventory(runtime)


def test_glibc_gate_records_maximum_and_rejects_newer_symbols(tmp_path, monkeypatch):
    package = _load_package_linux()
    binary = tmp_path / "runtime.so"
    _elf_x86_64(binary)
    monkeypatch.setattr(package, "_elf_files", lambda _root: iter((binary,)))
    monkeypatch.setattr(
        package,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="GLIBC_2.17 GLIBC_2.28"),
    )
    assert package._required_glibc(tmp_path) == (2, 28)

    monkeypatch.setattr(
        package,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="GLIBC_2.28 GLIBC_2.34"),
    )
    with pytest.raises(package.PackageError, match="newer than the supported"):
        package._required_glibc(tmp_path)


def test_payload_gate_allows_only_ctranslate2_cuda_native_libraries(tmp_path):
    package = _load_package_linux()
    runtime = tmp_path / "runtime"
    allowed = runtime / "_internal" / "ctranslate2"
    allowed.mkdir(parents=True)
    (allowed / "libcudnn.so.9").write_bytes(b"runtime")
    (runtime / "_internal" / "cv2" / "cuda").mkdir(parents=True)
    (runtime / "_internal" / "cv2" / "cuda" / "__init__.py").write_text(
        "# provider API", encoding="utf-8"
    )
    package._audit_runtime_payload(runtime)

    driver = runtime / "_internal" / "ctranslate2" / "libcuda.so.1"
    driver.write_bytes(b"driver")
    with pytest.raises(package.PackageError, match="GPU-driver"):
        package._audit_runtime_payload(runtime)
    driver.unlink()

    unrelated = runtime / "_internal" / "nvidia" / "lib" / "libcudart.so.13"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated stack")
    with pytest.raises(package.PackageError, match="forbidden Linux accelerator"):
        package._audit_runtime_payload(runtime)


def test_appdir_layout_uses_immutable_runtime_metadata_and_large_icon(tmp_path):
    package = _load_package_linux()
    runtime = _runtime(tmp_path, package)
    appdir = tmp_path / "Editarr.AppDir"

    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "LICENSE.txt").write_text("License text", encoding="utf-8")
    package._assemble_appdir(runtime, appdir, version="0.1.0", epoch=315532800, notices=notices)

    assert os.readlink(appdir / "usr" / "bin" / "Editarr") == "../lib/editarr/Editarr"
    assert os.readlink(appdir / f"{package.APP_ID}.desktop") == (
        f"usr/share/applications/{package.APP_ID}.desktop"
    )
    assert (appdir / "AppRun").stat().st_mode & 0o111
    assert (appdir / "usr" / "lib" / "editarr" / "Editarr").stat().st_mode & 0o111
    desktop = appdir / "usr" / "share" / "applications" / f"{package.APP_ID}.desktop"
    assert "Exec=Editarr" in desktop.read_text(encoding="utf-8")
    assert "X-AppImage-Version=0.1.0" in desktop.read_text(encoding="utf-8")
    metainfo = appdir / "usr" / "share" / "metainfo" / f"{package.APP_ID}.metainfo.xml"
    assert 'release version="0.1.0" date="1980-01-01"' in metainfo.read_text(
        encoding="utf-8"
    )
    icon = (
        appdir
        / "usr"
        / "share"
        / "icons"
        / "hicolor"
        / "1254x1254"
        / "apps"
        / "editarr.png"
    )
    assert icon.read_bytes() == package.ICON_SOURCE.read_bytes()
    license_file = appdir / "usr" / "share" / "doc" / "editarr" / "licenses" / "LICENSE.txt"
    assert license_file.read_text(encoding="utf-8") == "License text"
    assert (appdir / "editarr.png").resolve(strict=True) == icon
    assert (appdir / ".DirIcon").resolve(strict=True) == icon


@pytest.mark.parametrize("fail_build", [False, True])
def test_package_records_staged_outputs_only_after_success(tmp_path, monkeypatch, fail_build):
    package = _load_package_linux()
    runtime = _runtime(tmp_path, package)
    appimagetool = tmp_path / "appimagetool"
    appimage_runtime = tmp_path / "runtime-x86_64"
    appimagetool.write_bytes(b"tool")
    appimage_runtime.write_bytes(b"runtime")
    args = argparse.Namespace(
        runtime=runtime,
        out_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
        notices=None,
        appimagetool=appimagetool,
        appimage_runtime=appimage_runtime,
    )
    monkeypatch.setattr(package, "_require_linux_x86_64", lambda: None)
    monkeypatch.setattr(package, "_require_command", lambda _command: None)
    monkeypatch.setattr(package.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(package, "_audit_runtime_payload", lambda _runtime: None)
    monkeypatch.setattr(package, "_required_glibc", lambda _runtime: (2, 28))
    monkeypatch.setattr(package, "_require_pinned_file", lambda path, **_kwargs: path)

    def fail_tool(command, **_kwargs):
        if command[0] == str(appimagetool):
            if fail_build:
                raise package.subprocess.CalledProcessError(1, command)
            _elf_x86_64(Path(command[-1]))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(package, "_run", fail_tool)
    if fail_build:
        with pytest.raises(package.subprocess.CalledProcessError):
            package.package_linux(args)
        assert list(args.out_dir.iterdir()) == []
    else:
        assert package.package_linux(args) == 0
        evidence = json.loads((args.out_dir / "editarr-linux-package.json").read_text())
        appimage = args.out_dir / evidence["appimage"]
        appdir = args.out_dir / "Editarr.AppDir"
        assert evidence["appimage_size_bytes"] == appimage.stat().st_size > 0
        assert evidence["appimage_sha256"] == package._sha256(appimage)
        assert evidence["appdir_size_bytes"] == package._tree_size(appdir) > 0
        assert evidence["appdir_tree_sha256"] == package._tree_hash(appdir)


def test_apprun_uses_appdir_and_does_not_choose_a_writable_data_root():
    package = _load_package_linux()
    script = package.APPRUN_SOURCE.read_text(encoding="utf-8")

    assert 'exec "$APPDIR/usr/lib/editarr/Editarr" "$@"' in script
    assert "XDG_DATA_HOME" not in script
    assert "HOME=" not in script
    assert "systemctl" not in script


def test_linux_workflow_pins_baseline_and_covers_fuse_headless_and_graphical_paths():
    workflow = Path(".github/workflows/native14-linux-package.yml").read_text(
        encoding="utf-8"
    )

    baseline = Path("native_build/linux/build_package_ci.sh").read_text(encoding="utf-8")
    newer = Path("native_build/linux/verify_package_ci.sh").read_text(encoding="utf-8")

    assert "rockylinux/rockylinux@sha256:" in workflow
    assert 'test "$(getconf GNU_LIBC_VERSION)" = "glibc 2.28"' in baseline
    assert "--target linux-x86_64 --source-media-release" in baseline
    assert "native14-baseline-raw-verifier.json" in baseline
    assert "native14-baseline-appdir-verifier.json" in baseline
    assert "--appimage-extract-and-run paths" in baseline
    assert "native14-ubuntu-appdir-verifier.json" in newer
    assert '"$appimage" paths' in newer
    assert "env -u DISPLAY -u WAYLAND_DISPLAY -u BROWSER" in newer
    assert "Xvfb :99" in newer
    assert "lifecycle quit" in newer
    assert "test -c /dev/fuse" in newer
    assert "actions/upload-artifact@v7" in workflow
    assert "workflow_call:" in workflow
    assert "name: editarr-v${{ inputs.version }}-linux" in workflow
