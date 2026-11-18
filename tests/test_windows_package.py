"""Focused package-contract tests for NATIVE-13."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from native_build.windows import package_identity


def _packager():
    path = Path("scripts/package_windows.py")
    spec = importlib.util.spec_from_file_location("windows_packager", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pe(
    path: Path, *, subsystem: int = 3, machine: int = 0x8664, magic: int = 0x20B
) -> None:
    value = bytearray(512)
    value[:2] = b"MZ"
    struct.pack_into("<I", value, 0x3C, 0x80)
    value[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", value, 0x84, machine)
    struct.pack_into("<H", value, 0x98, magic)
    struct.pack_into("<H", value, 0x98 + 68, subsystem)
    path.write_bytes(value)


def test_windows_product_contract_is_stable_and_per_user():
    packager = _packager()

    assert packager.TARGET == "windows-x64"
    assert packager.APP_GUID == "{B1956A76-50E9-49E4-9A12-03683D04AB64}"
    assert packager.APP_ID == "{{B1956A76-50E9-49E4-9A12-03683D04AB64}"
    assert package_identity.DEVELOPMENT_PUBLISHER == (
        "Unsigned development build - no publisher identity"
    )
    assert packager.INSTALL_LOCATION == r"%LOCALAPPDATA%\Programs\Editarr"
    assert packager.DATA_LOCATION == r"%LOCALAPPDATA%\Editarr\Data"
    assert packager.CACHE_LOCATION == r"%LOCALAPPDATA%\Editarr\Cache"
    assert packager.LOG_LOCATION == r"%LOCALAPPDATA%\Editarr\Logs"


def test_inno_setup_is_non_elevated_data_preserving_and_lifecycle_aware():
    source = Path("native_build/windows/Editarr.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in source
    assert "DefaultDirName={localappdata}\\Programs\\Editarr" in source
    assert "ArchitecturesAllowed=x64compatible" in source
    assert "Launch Editarr when I sign in" in source
    assert "Editarr Diagnostics" in source
    assert 'Parameters: "--foreground"' in source
    assert "CompareVersions" in source
    assert "--quit" in source
    assert "uninsdeletevalue" in source
    assert "ProgramData" not in source
    assert "service" not in source.lower()
    assert "firewall" not in source.lower()
    assert "DelTree" not in source
    assert "UninstallDelete" not in source
    assert "ArtifactBaseFilename" in source
    assert "AppDisplayName" in source


def test_windows_evidence_uses_a_pinned_real_ffprobe_binary_not_a_chocolatey_shim():
    source = Path(".github/workflows/native13-windows-package.yml").read_text(
        encoding="utf-8"
    )

    assert "choco install ffmpeg --version=9.0.1" in source
    assert "lib\\ffmpeg\\tools" in source
    assert "ffprobeCandidates.Count -ne 1" in source
    assert "NATIVE13_FFPROBE_SHA256" in source
    assert "Get-Command ffprobe.exe" not in source


def test_windows_payload_audit_allows_only_ctranslate2_cuda_runtime():
    source = Path(".github/workflows/native13-windows-package.yml").read_text(
        encoding="utf-8"
    )

    assert "[IO.Path]::GetRelativePath($runtime, $_.FullName)" in source
    assert "$allowedCTranslate2Cuda" in source
    assert "ctranslate2" in source
    assert "$relative -match '(?i)^(data|credentials|corpora)" in source


def test_packager_allows_ctranslate2_cuda_but_rejects_other_gpu_stacks(tmp_path):
    packager = _packager()
    runtime = tmp_path / "Editarr"
    allowed = runtime / "_internal" / "ctranslate2" / "cudnn64_9.dll"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"packaged CUDA runtime")
    benign_source = runtime / "_internal" / "cv2" / "cuda" / "__init__.pyi"
    benign_source.parent.mkdir(parents=True)
    benign_source.write_text("# API stub", encoding="utf-8")

    packager._audit_runtime_payload(runtime)

    unrelated = runtime / "_internal" / "mlx" / "libmlx.dll"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"unrelated GPU stack")
    with pytest.raises(packager.PackageError, match="unapproved accelerator payload"):
        packager._audit_runtime_payload(runtime)

    unrelated.unlink()
    driver = runtime / "_internal" / "ctranslate2" / "nvcuda.dll"
    driver.write_bytes(b"GPU driver must remain host-provided")
    with pytest.raises(packager.PackageError, match="nvcuda.dll"):
        packager._audit_runtime_payload(runtime)


def test_unsigned_development_identity_is_explicit_and_cannot_borrow_production_options():
    packager = _packager()
    arguments = SimpleNamespace(
        unsigned_alpha=False,
        unsigned_development=True,
        publisher=None,
        certificate=None,
        timestamp_url=None,
    )

    identity = package_identity.resolve_identity(arguments, "0.1.0")

    assert identity.publisher == package_identity.DEVELOPMENT_PUBLISHER
    assert identity.development_only is True
    assert identity.signed is False
    assert identity.artifact_base == "Editarr-Setup-0.1.0-unsigned-development"
    assert "Unsigned Development Build" in identity.display_name

    arguments.publisher = "Unapproved Publisher"
    with pytest.raises(packager.PackageError, match="cannot be combined"):
        package_identity.resolve_identity(arguments, "0.1.0")


def test_production_identity_fails_closed_without_complete_signing_authority(tmp_path):
    packager = _packager()
    arguments = SimpleNamespace(
        unsigned_alpha=False,
        unsigned_development=False,
        publisher=None,
        certificate=None,
        timestamp_url=None,
    )
    with pytest.raises(packager.PackageError, match="approved Windows Publisher"):
        package_identity.resolve_identity(arguments, "0.1.0")

    arguments.publisher = "Approved Publisher"
    with pytest.raises(packager.PackageError, match="requires --certificate"):
        package_identity.resolve_identity(arguments, "0.1.0")

    arguments.certificate = tmp_path / "identity.p12"
    arguments.timestamp_url = "http://timestamp.test"
    with pytest.raises(packager.PackageError, match="must use HTTPS"):
        package_identity.resolve_identity(arguments, "0.1.0")

    arguments.timestamp_url = "https://timestamp.test"
    identity = package_identity.resolve_identity(arguments, "0.1.0")
    assert identity.signed is True
    assert identity.development_only is False
    assert identity.publisher == "Approved Publisher"


def test_unsigned_alpha_identity_is_labeled_and_cannot_borrow_signing_options():
    packager = _packager()
    arguments = SimpleNamespace(
        unsigned_alpha=True,
        unsigned_development=False,
        publisher=None,
        certificate=None,
        timestamp_url=None,
    )

    identity = package_identity.resolve_identity(arguments, "0.1.5")

    assert identity.publisher == package_identity.ALPHA_PUBLISHER
    assert identity.development_only is False
    assert identity.signed is False
    assert identity.artifact_base == "Editarr-v0.1.5-Windows-x64-Setup"
    assert "Unsigned Alpha" in identity.display_name

    arguments.publisher = "Publisher"
    with pytest.raises(packager.PackageError, match="cannot be combined"):
        package_identity.resolve_identity(arguments, "0.1.5")


def test_launcher_exposes_required_tray_actions_without_network_or_service_control():
    source = Path("native_build/windows/EditarrLauncher.cpp").read_text(encoding="utf-8")

    for label in (
        "Status: Running",
        "Status: Stopped",
        "Open Editarr",
        "Open Logs",
        "Start",
        "Stop",
        "Launch at Login",
        "Quit",
    ):
        assert f'L"{label}"' in source
    assert "Shell_NotifyIconW" in source
    assert 'L"lifecycle status"' in source
    assert 'L"lifecycle open-ui"' in source
    assert 'L"lifecycle open-logs"' in source
    assert 'L"lifecycle quit"' in source
    assert "CREATE_NO_WINDOW" in source
    assert "CreateService" not in source
    assert "firewall" not in source.lower()


def test_runtime_inventory_requires_windows_x64_console_entrypoint(tmp_path, monkeypatch):
    packager = _packager()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    executable = runtime / "Editarr.exe"
    _pe(executable, subsystem=3)
    (runtime / "editarr-runtime-inventory.json").write_text(
        json.dumps(
            {
                "format": 1,
                "target": "windows-x64",
                "version": "0.1.0",
                "source_revision": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(packager, "_project_version", lambda: "0.1.0")

    inventory = packager._read_runtime_inventory(runtime)

    assert inventory["target"] == "windows-x64"


@pytest.mark.parametrize(
    ("machine", "magic", "subsystem", "message"),
    [
        (0x14C, 0x20B, 3, r"not PE32\+ x64"),
        (0x8664, 0x10B, 3, r"not PE32\+ x64"),
        (0x8664, 0x20B, 2, "subsystem 2"),
    ],
)
def test_runtime_inventory_rejects_wrong_pe_identity(
    tmp_path, monkeypatch, machine, magic, subsystem, message
):
    packager = _packager()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    _pe(runtime / "Editarr.exe", machine=machine, magic=magic, subsystem=subsystem)
    (runtime / "editarr-runtime-inventory.json").write_text(
        json.dumps({"format": 1, "target": "windows-x64", "version": "0.1.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(packager, "_project_version", lambda: "0.1.0")

    with pytest.raises(packager.PackageError, match=message):
        packager._read_runtime_inventory(runtime)


def test_packaging_values_reject_newlines_and_signing_password_is_not_displayed(
    tmp_path, monkeypatch
):
    packager = _packager()
    with pytest.raises(packager.PackageError, match="invalid Inno Setup value"):
        packager._iscc_define("Publisher", "unsafe\nvalue")

    certificate = tmp_path / "identity.p12"
    certificate.write_bytes(b"test")
    executable = tmp_path / "Editarr.exe"
    executable.write_bytes(b"test")
    calls: list[tuple[list[str], list[str] | None]] = []

    def record(command, **kwargs):
        calls.append((command, kwargs.get("display")))

    monkeypatch.setattr(packager, "_run", record)
    monkeypatch.setenv("EDITARR_AUTHENTICODE_PASSWORD", "private-test-password")
    packager._sign(executable, certificate=certificate, timestamp_url="https://timestamp.test")

    sign_command, sign_display = calls[0]
    assert "private-test-password" in sign_command
    assert sign_display is not None
    assert "private-test-password" not in sign_display
    assert "[REDACTED]" in sign_display
