#!/usr/bin/env python3
"""Wrap a verified Windows x64 runtime in the per-user Editarr installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from native_build.artifact_size import SIZE_ACCOUNTING, write_sized_inventory
from native_build.artifact_size import tree_size as _tree_size

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SOURCE = ROOT / "native_build" / "windows" / "EditarrLauncher.cpp"
INNO_SOURCE = ROOT / "native_build" / "windows" / "Editarr.iss"
ICON_SOURCE = ROOT / "web" / "public" / "favicon.ico"
TARGET = "windows-x64"
APP_GUID = "{B1956A76-50E9-49E4-9A12-03683D04AB64}"
APP_ID = "{" + APP_GUID
DEVELOPMENT_PUBLISHER = "Unsigned development build - no publisher identity"
ALPHA_PUBLISHER = "Unsigned alpha build - no publisher identity"
INSTALL_LOCATION = r"%LOCALAPPDATA%\Programs\Editarr"
DATA_LOCATION = r"%LOCALAPPDATA%\Editarr\Data"
CACHE_LOCATION = r"%LOCALAPPDATA%\Editarr\Cache"
LOG_LOCATION = r"%LOCALAPPDATA%\Editarr\Logs"
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
_PUBLISHER = re.compile(r"^[\w .,&'()\-]{1,128}$", re.UNICODE)


class PackageError(RuntimeError):
    """A Windows package input or tool result is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    publisher: str
    signed: bool
    development_only: bool
    artifact_base: str
    display_name: str
    description: str


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    display: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(display or command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=capture,
        text=True,
    )


def _require_windows_x64() -> None:
    if os.name != "nt":
        raise PackageError("Windows packages must be built natively on Windows x64")
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    if machine not in {"amd64", "x86_64"}:
        raise PackageError(f"unsupported Windows packaging architecture: {machine or 'unknown'}")


def _require_commands(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise PackageError("missing Windows packaging commands: " + ", ".join(missing))


def _project_version() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata["project"]["version"])
    if _VERSION.fullmatch(version) is None:
        raise PackageError("project version is not valid Windows version metadata")
    return version


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(value) for value in version.split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_runtime_inventory(runtime: Path) -> dict[str, Any]:
    path = runtime / "editarr-runtime-inventory.json"
    if path.is_symlink() or not path.is_file():
        raise PackageError("runtime inventory must be a regular file at the runtime root")
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackageError("runtime inventory is invalid") from error
    if inventory.get("format") != 1 or inventory.get("target") != TARGET:
        raise PackageError(f"runtime inventory must identify target {TARGET}")
    if inventory.get("version") != _project_version():
        raise PackageError("runtime version does not match pyproject.toml")
    executable = runtime / "Editarr.exe"
    if executable.is_symlink() or not executable.is_file():
        raise PackageError("runtime does not contain a regular Editarr.exe entry point")
    _require_pe_x64(executable, expected_subsystem=3)
    return inventory


def _pe_headers(path: Path) -> tuple[int, int, int]:
    try:
        with path.open("rb") as source:
            if source.read(2) != b"MZ":
                raise PackageError(f"not a Windows PE executable: {path}")
            source.seek(0x3C)
            pe_offset = struct.unpack("<I", source.read(4))[0]
            source.seek(pe_offset)
            if source.read(4) != b"PE\0\0":
                raise PackageError(f"invalid Windows PE header: {path}")
            machine = struct.unpack("<H", source.read(2))[0]
            source.seek(pe_offset + 24)
            magic = struct.unpack("<H", source.read(2))[0]
            source.seek(pe_offset + 24 + 68)
            subsystem = struct.unpack("<H", source.read(2))[0]
    except (OSError, struct.error) as error:
        raise PackageError(f"could not inspect Windows executable: {path}") from error
    return machine, magic, subsystem


def _require_pe_x64(path: Path, *, expected_subsystem: int | None = None) -> None:
    """Validate PE32+ x64 identity without relying on host-local inspection tools."""

    machine, magic, subsystem = _pe_headers(path)
    if machine != 0x8664 or magic != 0x20B:
        raise PackageError(f"Windows executable is not PE32+ x64: {path}")
    if expected_subsystem is not None and subsystem != expected_subsystem:
        raise PackageError(
            f"Windows executable has subsystem {subsystem}, expected {expected_subsystem}: {path}"
        )


def _require_windows_gui(path: Path) -> None:
    """Accept Inno's x86 bootstrap or x64 bootstrap, but never a console/foreign PE."""

    machine, magic, subsystem = _pe_headers(path)
    if (machine, magic) not in {(0x14C, 0x10B), (0x8664, 0x20B)}:
        raise PackageError(f"Windows installer has an unsupported PE architecture: {path}")
    if subsystem != 2:
        raise PackageError(f"Windows installer is not a GUI executable: {path}")


def _reject_links(root: Path) -> None:
    links = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    if links:
        raise PackageError("Windows runtime may not contain links: " + ", ".join(links[:8]))


_ACCELERATOR_NAME = re.compile(r"mlx|cuda|cudnn|cublas|cudart|nvrtc|nvjitlink|nvidia", re.I)
_NATIVE_BINARY = re.compile(r"\.(?:dll|pyd|so|dylib)$", re.I)
_MLX_PACKAGE = re.compile(r"^_internal/mlx(?:[_-][^/]*)?(?:/|$)", re.I)
_ALLOWED_CTRANSLATE2_CUDA = re.compile(
    r"^_internal/ctranslate2/(?:cudnn|cublas|cudart|nvrtc|nvjitlink)[^/]*\.dll$",
    re.I,
)


def _audit_runtime_payload(runtime: Path) -> None:
    """Allow only CTranslate2's packaged CUDA runtime and never an NVIDIA driver.

    CUDA is optional at execution time.  The runtime capability probe must still prove a
    compatible host driver before selecting it; this file audit only constrains what the
    installer is allowed to carry.
    """

    forbidden: list[str] = []
    for path in runtime.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(runtime).as_posix()
        is_mlx_package = _MLX_PACKAGE.match(relative) is not None
        is_accelerator_binary = bool(
            _NATIVE_BINARY.search(path.name) and _ACCELERATOR_NAME.search(relative)
        )
        if not is_mlx_package and not is_accelerator_binary:
            continue
        if _ALLOWED_CTRANSLATE2_CUDA.fullmatch(relative):
            continue
        forbidden.append(relative)
    if forbidden:
        raise PackageError(
            "Windows runtime contains an unapproved accelerator payload: "
            + ", ".join(sorted(forbidden)[:8])
        )


def _refresh_runtime_size(runtime: Path, inventory: dict[str, Any]) -> None:
    write_sized_inventory(runtime / "editarr-runtime-inventory.json", inventory)


def _resource_text(*, icon: Path, version: str, publisher: str) -> str:
    numeric = ",".join(str(value) for value in _version_tuple(version))
    escaped_icon = str(icon).replace("\\", "\\\\").replace('"', '\\"')
    safe_publisher = publisher.replace("\\", "\\\\").replace('"', '\\"')
    return f'''#include <windows.h>
1 ICON "{escaped_icon}"
1 VERSIONINFO
 FILEVERSION {numeric}
 PRODUCTVERSION {numeric}
 FILEFLAGSMASK 0x3fL
 FILEFLAGS 0x0L
 FILEOS VOS_NT_WINDOWS32
 FILETYPE VFT_APP
 FILESUBTYPE VFT2_UNKNOWN
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "{safe_publisher}\\0"
      VALUE "FileDescription", "Editarr lifecycle launcher\\0"
      VALUE "FileVersion", "{version}\\0"
      VALUE "InternalName", "Editarr\\0"
      VALUE "OriginalFilename", "Editarr.exe\\0"
      VALUE "ProductName", "Editarr\\0"
      VALUE "ProductVersion", "{version}\\0"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
'''


def _build_launcher(stage: Path, build_root: Path, *, version: str, publisher: str) -> Path:
    resource = build_root / "EditarrLauncher.rc"
    compiled_resource = build_root / "EditarrLauncher.res"
    resource.write_text(
        _resource_text(icon=ICON_SOURCE.resolve(), version=version, publisher=publisher),
        encoding="utf-8",
    )
    _run(["rc.exe", "/nologo", f"/fo{compiled_resource}", str(resource)], cwd=ROOT)
    launcher = stage / "Editarr.exe"
    _run(
        [
            "cl.exe",
            "/nologo",
            "/std:c++17",
            "/EHsc",
            "/O2",
            "/MT",
            f"/Fo{build_root / 'EditarrLauncher.obj'}",
            str(LAUNCHER_SOURCE),
            str(compiled_resource),
            f"/Fe{launcher}",
            "/link",
            "/SUBSYSTEM:WINDOWS,6.01",
            "shell32.lib",
            "user32.lib",
            "advapi32.lib",
        ],
        cwd=ROOT,
    )
    _require_pe_x64(launcher, expected_subsystem=2)
    return launcher


def _sign(path: Path, *, certificate: Path, timestamp_url: str) -> None:
    password = os.environ.get("EDITARR_AUTHENTICODE_PASSWORD")
    command = [
        "signtool.exe",
        "sign",
        "/fd",
        "SHA256",
        "/td",
        "SHA256",
        "/tr",
        timestamp_url,
        "/f",
        str(certificate),
    ]
    display = list(command)
    if password:
        command.extend(["/p", password])
        display.extend(["/p", "[REDACTED]"])
    command.append(str(path))
    display.append(str(path))
    _run(command, display=display)
    _run(["signtool.exe", "verify", "/pa", "/all", "/v", str(path)])


def _iscc_define(name: str, value: str) -> str:
    if not value or any(character in value for character in ("\r", "\n")):
        raise PackageError(f"invalid Inno Setup value for {name}")
    return f"/D{name}={value}"


def _resolve_identity(args: argparse.Namespace, version: str) -> PackageIdentity:
    if args.unsigned_alpha:
        if args.publisher or args.certificate or args.timestamp_url:
            raise PackageError(
                "--unsigned-alpha cannot be combined with production identity/signing options"
            )
        return PackageIdentity(
            publisher=ALPHA_PUBLISHER,
            signed=False,
            development_only=False,
            artifact_base=f"Editarr-v{version}-Windows-x64-Setup",
            display_name="Editarr (Unsigned Alpha)",
            description="Editarr per-user installer - unsigned alpha release",
        )

    if args.unsigned_development:
        if args.publisher or args.certificate or args.timestamp_url:
            raise PackageError(
                "--unsigned-development cannot be combined with production identity/signing options"
            )
        return PackageIdentity(
            publisher=DEVELOPMENT_PUBLISHER,
            signed=False,
            development_only=True,
            artifact_base=f"Editarr-Setup-{version}-unsigned-development",
            display_name="Editarr (Unsigned Development Build)",
            description="Editarr per-user installer - unsigned development build",
        )

    if _PUBLISHER.fullmatch(args.publisher or "") is None:
        raise PackageError("an approved Windows Publisher display name is required")
    if not args.certificate or not args.timestamp_url:
        raise PackageError(
            "production packaging requires --certificate and an HTTPS --timestamp-url"
        )
    if urlsplit(args.timestamp_url).scheme.lower() != "https":
        raise PackageError("--timestamp-url must use HTTPS")
    return PackageIdentity(
        publisher=args.publisher,
        signed=True,
        development_only=False,
        artifact_base=f"Editarr-Setup-{version}",
        display_name="Editarr",
        description="Editarr per-user installer",
    )


def package_windows(args: argparse.Namespace) -> int:
    _require_windows_x64()
    _require_commands("cl.exe", "rc.exe", "ISCC.exe")
    version = _project_version()
    identity = _resolve_identity(args, version)
    if identity.signed:
        _require_commands("signtool.exe")
        assert args.certificate
        if args.certificate.is_symlink() or not args.certificate.is_file():
            raise PackageError("--certificate must name a regular PKCS#12 file")

    runtime = args.runtime.resolve()
    inventory = _read_runtime_inventory(runtime)
    _reject_links(runtime)
    _audit_runtime_payload(runtime)
    notices = args.notices
    if args.unsigned_alpha and notices is None:
        raise PackageError("--unsigned-alpha requires --notices")
    if notices is not None:
        if notices.is_symlink() or not (notices / "THIRD-PARTY-NOTICES.md").is_file():
            raise PackageError("--notices must name a directory with THIRD-PARTY-NOTICES.md")
        _reject_links(notices)
    output = args.out_dir.resolve()
    installer = output / f"{identity.artifact_base}.exe"
    evidence_path = output / "editarr-windows-package.json"
    if installer.exists() or evidence_path.exists():
        raise PackageError("refusing to overwrite an existing Windows package output")
    output.mkdir(parents=True, exist_ok=True)
    work_parent = args.work_dir.resolve()
    work_parent.mkdir(parents=True, exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix="editarr-windows-package-", dir=work_parent))
    try:
        stage = build_root / "stage"
        stage.mkdir()
        staged_runtime = stage / "Runtime"
        shutil.copytree(runtime, staged_runtime)
        shutil.copy2(ICON_SOURCE, stage / "Editarr.ico")
        if notices is not None:
            shutil.copytree(notices, stage / "Licenses")
        launcher = _build_launcher(
            stage, build_root, version=version, publisher=identity.publisher
        )

        if identity.signed:
            assert args.certificate and args.timestamp_url
            _sign(
                staged_runtime / "Editarr.exe",
                certificate=args.certificate,
                timestamp_url=args.timestamp_url,
            )
            _sign(launcher, certificate=args.certificate, timestamp_url=args.timestamp_url)
            _refresh_runtime_size(staged_runtime, inventory)

        _run(
            [
                "ISCC.exe",
                "/Qp",
                _iscc_define("StageRoot", str(stage)),
                _iscc_define("OutputDir", str(output)),
                _iscc_define("AppVersion", version),
                _iscc_define("AppPublisher", identity.publisher),
                _iscc_define("AppId", APP_ID),
                _iscc_define("AppGuid", APP_GUID),
                _iscc_define("AppDisplayName", identity.display_name),
                _iscc_define("ArtifactBaseFilename", identity.artifact_base),
                _iscc_define("BuildDescription", identity.description),
                str(INNO_SOURCE),
            ],
            cwd=ROOT,
        )
        if not installer.is_file():
            raise PackageError("Inno Setup did not create the expected installer")
        _require_windows_gui(installer)
        if identity.signed:
            assert args.certificate and args.timestamp_url
            _sign(installer, certificate=args.certificate, timestamp_url=args.timestamp_url)

        evidence = {
            "format": 1,
            "target": TARGET,
            "version": version,
            "source_revision": inventory.get("source_revision"),
            "app_id": APP_GUID,
            "publisher_metadata": identity.publisher,
            "publisher_identity_approved": identity.signed,
            "install_location": INSTALL_LOCATION,
            "data_location": DATA_LOCATION,
            "cache_location": CACHE_LOCATION,
            "log_location": LOG_LOCATION,
            "tray_technology": "Win32 Shell_NotifyIconW lifecycle shell",
            "launch_at_login": "HKCU Run value, optional installer task and tray toggle",
            "normal_console": False,
            "diagnostic_command": r"Runtime\Editarr.exe --foreground",
            "signed": identity.signed,
            "development_only": identity.development_only,
            "build_description": identity.description,
            "timestamp_url": args.timestamp_url if identity.signed else None,
            "launcher_sha256": _sha256(launcher),
            "runtime_entrypoint_sha256": _sha256(staged_runtime / "Editarr.exe"),
            "staged_size_bytes": _tree_size(stage),
            "size_accounting": SIZE_ACCOUNTING,
            "installer": installer.name,
            "installer_sha256": _sha256(installer),
            "installer_size_bytes": installer.stat().st_size,
        }
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(build_root, ignore_errors=True)

    print(f"Windows installer written to {installer}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Verified Windows onedir root")
    parser.add_argument("--out-dir", type=Path, required=True, help="Installer output directory")
    parser.add_argument("--publisher", help="Approved production Publisher display name")
    parser.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--certificate", type=Path, help="Authorized Authenticode PKCS#12 file")
    parser.add_argument("--timestamp-url", help="Authorized RFC 3161 timestamp service")
    parser.add_argument("--notices", type=Path, help="License notices to install with Editarr")
    unsigned = parser.add_mutually_exclusive_group()
    unsigned.add_argument(
        "--unsigned-development",
        action="store_true",
        help="Build visibly labeled unsigned development output with no publisher identity",
    )
    unsigned.add_argument(
        "--unsigned-alpha",
        action="store_true",
        help="Build the unsigned alpha release installer with no publisher identity",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return package_windows(_parse_args())
    except (PackageError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
