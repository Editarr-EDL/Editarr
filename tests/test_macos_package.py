"""Focused contract tests for the macOS application wrapper."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import subprocess
from pathlib import Path

import pytest


def _package_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "package_macos.py"
    spec = importlib.util.spec_from_file_location("package_macos", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_macho_record_retains_exact_deployment_load_command(monkeypatch, tmp_path: Path):
    package = _package_module()
    object_path = tmp_path / "runtime" / "_internal" / "libexample.dylib"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"not examined because commands are mocked")

    def command(command: list[str], **_kwargs):
        if command[:2] == ["file", "-b"]:
            return subprocess.CompletedProcess(
                command, 0, "Mach-O 64-bit dynamically linked shared library\n", ""
            )
        if command[:2] == ["lipo", "-archs"]:
            return subprocess.CompletedProcess(command, 0, "arm64\n", "")
        assert command[:2] == ["otool", "-l"]
        return subprocess.CompletedProcess(
            command,
            0,
            "Load command 0\n"
            "      cmd LC_BUILD_VERSION\n"
            "  cmdsize 32\n"
            " platform 1\n"
            "    minos 26.2\n"
            "      sdk 27.0\n",
            "",
        )

    monkeypatch.setattr(package, "_run", command)
    record = package._macho_record(object_path, root=tmp_path / "runtime")

    assert record == {
        "path": "_internal/libexample.dylib",
        "architectures": ["arm64"],
        "minimum_macos": "26.2",
        "deployment_load_commands": [
            "Load command 0\n"
            "      cmd LC_BUILD_VERSION\n"
            "  cmdsize 32\n"
            " platform 1\n"
            "    minos 26.2\n"
            "      sdk 27.0"
        ],
    }


def test_macho_record_ignores_non_deployment_dylib_version(monkeypatch, tmp_path: Path):
    package = _package_module()
    object_path = tmp_path / "runtime" / "libexample.dylib"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"x")

    def command(command: list[str], **_kwargs):
        if command[:2] == ["file", "-b"]:
            return subprocess.CompletedProcess(command, 0, "Mach-O 64-bit executable\n", "")
        if command[:2] == ["lipo", "-archs"]:
            return subprocess.CompletedProcess(command, 0, "arm64\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "Load command 0\n"
            "      cmd LC_ID_DYLIB\n"
            "  cmdsize 56\n"
            "  current version 61421.0.0\n"
            "Load command 1\n"
            "      cmd LC_BUILD_VERSION\n"
            "  cmdsize 32\n"
            " platform 1\n"
            "    minos 26.2\n"
            "      sdk 27.0\n",
            "",
        )

    monkeypatch.setattr(package, "_run", command)
    record = package._macho_record(object_path, root=tmp_path / "runtime")
    assert record is not None
    assert record["minimum_macos"] == "26.2"


def test_macho_record_ignores_build_tool_version(monkeypatch, tmp_path: Path):
    package = _package_module()
    object_path = tmp_path / "runtime" / "libexample.dylib"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"x")

    def command(command: list[str], **_kwargs):
        if command[:2] == ["file", "-b"]:
            return subprocess.CompletedProcess(command, 0, "Mach-O 64-bit executable\n", "")
        if command[:2] == ["lipo", "-archs"]:
            return subprocess.CompletedProcess(command, 0, "arm64\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "Load command 0\n"
            "      cmd LC_BUILD_VERSION\n"
            "  cmdsize 32\n"
            " platform 1\n"
            "    minos 26.2\n"
            "      sdk 27.0\n"
            "    ntools 1\n"
            "      tool 3\n"
            "   version 1267.0\n",
            "",
        )

    monkeypatch.setattr(package, "_run", command)
    record = package._macho_record(object_path, root=tmp_path / "runtime")
    assert record is not None
    assert record["minimum_macos"] == "26.2"


def test_minimum_macos_records_all_forcing_objects():
    package = _package_module()
    minimum, forcing = package._minimum_macos(
        [
            {"path": "a", "minimum_macos": "15.0", "deployment_load_commands": ["old"]},
            {"path": "z", "minimum_macos": "26.2", "deployment_load_commands": ["z load"]},
            {"path": "b", "minimum_macos": "26.2", "deployment_load_commands": ["b load"]},
        ]
    )

    assert minimum == "26.2"
    assert forcing == [
        {"path": "b", "minimum_macos": "26.2", "deployment_load_commands": ["b load"]},
        {"path": "z", "minimum_macos": "26.2", "deployment_load_commands": ["z load"]},
    ]


def test_runtime_records_reject_universal_or_other_architectures(monkeypatch, tmp_path: Path):
    package = _package_module()
    executable = tmp_path / "Editarr"
    executable.write_bytes(b"x")

    monkeypatch.setattr(
        package,
        "_macho_record",
        lambda path, **_kwargs: {
            "path": path.name,
            "architectures": ["arm64", "x86_64"],
            "minimum_macos": "15.0",
            "deployment_load_commands": [],
        },
    )
    with pytest.raises(package.PackageError, match="non-arm64-only"):
        package._runtime_macho_records(tmp_path)


def test_validate_app_layout_requires_menu_agent_contract(tmp_path: Path):
    package = _package_module()
    contents = tmp_path / "Editarr.app" / "Contents"
    runtime = contents / "Resources" / "runtime" / "Editarr"
    launcher = contents / "MacOS" / "Editarr"
    runtime.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    for executable in (launcher, runtime / "Editarr"):
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    (contents / "Resources" / "Editarr.icns").write_bytes(b"icon")
    with (contents / "Info.plist").open("wb") as destination:
        plistlib.dump(
            {
                "CFBundleExecutable": "Editarr",
                "CFBundleIdentifier": "org.editarr.app",
                "CFBundlePackageType": "APPL",
                "LSMinimumSystemVersion": "26.2",
                "LSUIElement": True,
                "LSRequiresNativeExecution": True,
            },
            destination,
        )

    package._validate_app_layout(tmp_path / "Editarr.app", minimum_macos="26.2")
    os.chmod(launcher, 0o644)
    with pytest.raises(package.PackageError, match="not executable"):
        package._validate_app_layout(tmp_path / "Editarr.app", minimum_macos="26.2")


def test_end_user_self_sign_commands_are_ad_hoc_and_verified():
    package = _package_module()
    app = "/Applications/Editarr.app"

    assert package._end_user_self_sign_commands(app) == [
        ["xattr", "-dr", "com.apple.quarantine", app],
        ["codesign", "--force", "--deep", "--sign", "-", app],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app],
    ]


def test_fake_model_runtime_requires_explicit_disposable_test_mode(tmp_path: Path):
    package = _package_module()
    marker = tmp_path / "_internal" / "native-test-fake-models.marker"
    marker.parent.mkdir()
    marker.write_text("test-only\n", encoding="utf-8")

    with pytest.raises(package.PackageError, match="test-only"):
        package._require_distributable_runtime(tmp_path, test_fake_models=False)

    package._require_distributable_runtime(tmp_path, test_fake_models=True)


def test_unsigned_policy_rejects_a_bundle_that_already_passes_strict_verification(
    monkeypatch, tmp_path: Path
):
    package = _package_module()
    monkeypatch.setattr(
        package,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    with pytest.raises(package.PackageError, match="end-user local self-signing"):
        package._assert_end_user_self_sign_required(tmp_path / "Editarr.app")
