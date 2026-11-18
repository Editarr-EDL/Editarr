#!/usr/bin/env python3
"""Assemble a verified Linux x86_64 runtime into a reproducible AppImage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from native_build.artifact_size import SIZE_ACCOUNTING
from native_build.artifact_size import tree_size as _tree_size

ROOT = Path(__file__).resolve().parent.parent
LINUX_INPUTS = ROOT / "native_build" / "linux"
APPRUN_SOURCE = LINUX_INPUTS / "AppRun"
DESKTOP_SOURCE = LINUX_INPUTS / "org.editarr.Editarr.desktop"
APPSTREAM_SOURCE = LINUX_INPUTS / "org.editarr.Editarr.metainfo.xml"
ICON_SOURCE = ROOT / "web" / "public" / "editarr-app-icon.png"
TARGET = "linux-x86_64"
APP_ID = "org.editarr.Editarr"
BASELINE_DISTRIBUTION = "Rocky Linux 8"
BASELINE_GLIBC = (2, 28)
DEFAULT_SOURCE_DATE_EPOCH = 315532800
# Require exact upstream release assets and record the corresponding source commits.
APPIMAGETOOL_RELEASE = "1.9.1"
APPIMAGETOOL_COMMIT = "8c8c91f762b412a19f4e8d2c4b35afb98f2d7c81"
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGE_RUNTIME_RELEASE = "20251108"
APPIMAGE_RUNTIME_COMMIT = "dd6cebedcbddde9c82f89b011e8e1d40b6e43868"
APPIMAGE_RUNTIME_SHA256 = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
_GLIBC_SYMBOL = re.compile(r"\bGLIBC_(\d+)\.(\d+)\b")
_NATIVE_SUFFIX = re.compile(r"(?i)(?:\.so(?:\.[0-9]+)*|\.dylib|\.dll|\.pyd)$")
_ACCELERATOR_NAME = re.compile(
    r"(?i)(?:^|[/_.-])(mlx|cuda|cudnn|cublas|cudart|nvrtc|nvjitlink|nvidia)(?:[/_.-]|$)"
)
_ALLOWED_CTRANSLATE2_CUDA = re.compile(
    r"(?i)^_internal/ctranslate2/(?:lib)?(?:cudnn|cublas|cudart|nvrtc|nvjitlink)[^/]*"
    r"(?:\.so(?:\.[0-9]+)*|\.dll)$"
)


class PackageError(RuntimeError):
    """A Linux package input, payload, or tool result is unsafe or incomplete."""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=capture,
        text=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.readlink(path).encode() + b"\0")
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def _project_version() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata["project"]["version"])
    if _VERSION.fullmatch(version) is None:
        raise PackageError("project version is not valid AppImage metadata")
    return version


def _require_linux_x86_64() -> None:
    if sys.platform != "linux":
        raise PackageError("AppImages must be built natively on Linux x86_64")
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise PackageError(
            f"unsupported Linux packaging architecture: {platform.machine() or 'unknown'}"
        )


def _require_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise PackageError(f"{command} is required for Linux AppImage packaging")
    return resolved


def _appstream_validation_command(path: Path) -> list[str]:
    if appstreamcli := shutil.which("appstreamcli"):
        return [appstreamcli, "validate", "--no-net", str(path)]
    if appstream_util := shutil.which("appstream-util"):
        return [appstream_util, "validate-relax", str(path)]
    raise PackageError(
        "appstreamcli or appstream-util is required for Linux AppImage packaging"
    )


def _require_pinned_file(path: Path, *, label: str, expected_sha256: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise PackageError(f"{label} must be a regular pinned file")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise PackageError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, found {actual}"
        )
    return path


def _require_elf_x86_64(path: Path) -> None:
    try:
        header = path.read_bytes()[:20]
        if len(header) < 20 or header[:4] != b"\x7fELF":
            raise PackageError(f"not an ELF executable: {path}")
        if header[4:6] != b"\x02\x01":
            raise PackageError(f"Linux executable is not little-endian ELF64: {path}")
        machine = struct.unpack_from("<H", header, 18)[0]
    except OSError as error:
        raise PackageError(f"could not inspect Linux executable: {path}") from error
    if machine != 62:
        raise PackageError(f"Linux executable is not x86_64 ELF: {path}")


def _read_runtime_inventory(runtime: Path) -> dict[str, Any]:
    inventory_path = runtime / "editarr-runtime-inventory.json"
    if inventory_path.is_symlink() or not inventory_path.is_file():
        raise PackageError("runtime inventory must be a regular file at the runtime root")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackageError("runtime inventory is invalid") from error
    if inventory.get("format") != 1 or inventory.get("target") != TARGET:
        raise PackageError(f"runtime inventory must identify target {TARGET}")
    if inventory.get("version") != _project_version():
        raise PackageError("runtime version does not match pyproject.toml")
    executable = runtime / "Editarr"
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise PackageError("runtime does not contain a regular executable Editarr entry point")
    _require_elf_x86_64(executable)
    return inventory


def _audit_runtime_payload(runtime: Path) -> None:
    """Reject drivers and unrelated accelerator stacks while retaining CT2 CUDA.

    Python packages may legitimately expose modules named ``cuda`` even in a CPU build,
    so this gate examines native payloads and top-level MLX/NVIDIA packages rather than
    matching arbitrary source/resource filenames.
    """

    forbidden: list[str] = []
    for path in runtime.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(runtime).as_posix()
        lowered = relative.lower()
        top_level_accelerator = lowered.startswith(("_internal/mlx", "_internal/nvidia/"))
        accelerator_binary = bool(
            _NATIVE_SUFFIX.search(path.name) and _ACCELERATOR_NAME.search(relative)
        )
        allowed_ctranslate2 = bool(_ALLOWED_CTRANSLATE2_CUDA.fullmatch(relative))
        driver = path.name.lower() in {"libcuda.so", "libcuda.so.1", "nvcuda.dll"}
        if driver or top_level_accelerator or (accelerator_binary and not allowed_ctranslate2):
            forbidden.append(relative)
    if forbidden:
        raise PackageError(
            "forbidden Linux accelerator or GPU-driver payload: "
            + ", ".join(sorted(forbidden)[:20])
        )


def _elf_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as source:
                if source.read(4) == b"\x7fELF":
                    yield path
        except OSError as error:
            raise PackageError(f"could not inspect runtime payload: {path}") from error


def _required_glibc(runtime: Path) -> tuple[int, int]:
    versions: set[tuple[int, int]] = set()
    for path in _elf_files(runtime):
        result = _run(["readelf", "--version-info", str(path)], capture=True)
        versions.update(
            (int(major), int(minor))
            for major, minor in _GLIBC_SYMBOL.findall(result.stdout)
        )
    if not versions:
        raise PackageError("runtime ELF payload did not declare any GLIBC symbol requirements")
    maximum = max(versions)
    if maximum > BASELINE_GLIBC:
        raise PackageError(
            "runtime requires GLIBC_"
            f"{maximum[0]}.{maximum[1]}, newer than the supported GLIBC_2.28 baseline"
        )
    return maximum


def _render_template(source: Path, *, version: str, release_date: str) -> str:
    rendered = source.read_text(encoding="utf-8")
    rendered = rendered.replace("@VERSION@", version)
    rendered = rendered.replace("@RELEASE_DATE@", release_date)
    if "@VERSION@" in rendered or "@RELEASE_DATE@" in rendered:
        raise PackageError(f"unresolved Linux metadata token in {source.name}")
    return rendered


def _normalize_tree(root: Path, epoch: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            try:
                os.utime(path, (epoch, epoch), follow_symlinks=False)
            except (NotImplementedError, OSError):
                # Linux supports this operation; retaining the fallback keeps the pure
                # layout helper usable in platform-neutral unit tests.
                pass
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.name == "AppRun" or path == root / "usr" / "lib" / "editarr" / "Editarr":
            path.chmod(0o755)
        else:
            path.chmod(0o644 | (path.stat().st_mode & 0o111))
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    root.chmod(0o755)
    os.utime(root, (epoch, epoch), follow_symlinks=False)


def _assemble_appdir(runtime: Path, appdir: Path, *, version: str, epoch: int) -> None:
    embedded = appdir / "usr" / "lib" / "editarr"
    shutil.copytree(runtime, embedded, symlinks=True)
    applications = appdir / "usr" / "share" / "applications"
    metainfo = appdir / "usr" / "share" / "metainfo"
    icons = appdir / "usr" / "share" / "icons" / "hicolor" / "1254x1254" / "apps"
    binary_dir = appdir / "usr" / "bin"
    for directory in (applications, metainfo, icons, binary_dir):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(APPRUN_SOURCE, appdir / "AppRun")
    release_date = datetime.fromtimestamp(epoch, UTC).date().isoformat()
    desktop = applications / f"{APP_ID}.desktop"
    desktop.write_text(
        _render_template(DESKTOP_SOURCE, version=version, release_date=release_date),
        encoding="utf-8",
    )
    appstream = metainfo / f"{APP_ID}.metainfo.xml"
    appstream.write_text(
        _render_template(APPSTREAM_SOURCE, version=version, release_date=release_date),
        encoding="utf-8",
    )
    shutil.copy2(ICON_SOURCE, icons / "editarr.png")

    (binary_dir / "Editarr").symlink_to("../lib/editarr/Editarr")
    (appdir / f"{APP_ID}.desktop").symlink_to(
        f"usr/share/applications/{APP_ID}.desktop"
    )
    (appdir / "editarr.png").symlink_to(
        (icons / "editarr.png").relative_to(appdir)
    )
    (appdir / ".DirIcon").symlink_to("editarr.png")
    _normalize_tree(appdir, epoch)


def package_linux(args: argparse.Namespace) -> int:
    _require_linux_x86_64()
    for command in ("readelf", "desktop-file-validate"):
        _require_command(command)
    version = _project_version()
    runtime = args.runtime.resolve()
    inventory = _read_runtime_inventory(runtime)
    _audit_runtime_payload(runtime)
    required_glibc = _required_glibc(runtime)
    appimagetool = _require_pinned_file(
        args.appimagetool.resolve(),
        label="appimagetool",
        expected_sha256=APPIMAGETOOL_SHA256,
    )
    appimage_runtime = _require_pinned_file(
        args.appimage_runtime.resolve(),
        label="AppImage runtime",
        expected_sha256=APPIMAGE_RUNTIME_SHA256,
    )
    output = args.out_dir.resolve()
    appdir = output / "Editarr.AppDir"
    appimage_name = f"Editarr-{version}-x86_64.AppImage"
    appimage = output / appimage_name
    checksum = output / f"{appimage_name}.sha256"
    evidence_path = output / "editarr-linux-package.json"
    package_outputs = (appdir, appimage, checksum, evidence_path)
    if any(path.exists() or path.is_symlink() for path in package_outputs):
        raise PackageError("refusing to overwrite existing Linux package output")
    output.mkdir(parents=True, exist_ok=True)
    work_parent = args.work_dir.resolve()
    work_parent.mkdir(parents=True, exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix="editarr-linux-package-", dir=work_parent))
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH))
    if epoch < 0:
        raise PackageError("SOURCE_DATE_EPOCH must be a non-negative integer")
    try:
        staged_appdir = build_root / "Editarr.AppDir"
        _assemble_appdir(runtime, staged_appdir, version=version, epoch=epoch)
        desktop = staged_appdir / "usr" / "share" / "applications" / f"{APP_ID}.desktop"
        appstream = staged_appdir / "usr" / "share" / "metainfo" / f"{APP_ID}.metainfo.xml"
        _run(["desktop-file-validate", str(desktop)])
        _run(_appstream_validation_command(appstream))

        environment = os.environ.copy()
        environment.update(
            {
                "ARCH": "x86_64",
                "VERSION": version,
                "SOURCE_DATE_EPOCH": str(epoch),
            }
        )
        _run(
            [
                str(appimagetool),
                "--appimage-extract-and-run",
                "--runtime-file",
                str(appimage_runtime),
                "--comp",
                "zstd",
                str(staged_appdir),
                str(build_root / appimage_name),
            ],
            cwd=build_root,
            environment=environment,
        )
        staged_appimage = build_root / appimage_name
        _require_elf_x86_64(staged_appimage)
        staged_appimage.chmod(0o755)
        appimage_hash = _sha256(staged_appimage)
        runtime_inventory = runtime / "editarr-runtime-inventory.json"
        evidence = {
            "format": "editarr-linux-package-v1",
            "target": TARGET,
            "version": version,
            "source_revision": inventory.get("source_revision"),
            "baseline_distribution": BASELINE_DISTRIBUTION,
            "baseline_glibc": f"{BASELINE_GLIBC[0]}.{BASELINE_GLIBC[1]}",
            "required_glibc_max": f"{required_glibc[0]}.{required_glibc[1]}",
            "app_id": APP_ID,
            "appimage": appimage.name,
            "appimage_sha256": appimage_hash,
            "appimage_size_bytes": staged_appimage.stat().st_size,
            "appdir_tree_sha256": _tree_hash(staged_appdir),
            "appdir_size_bytes": _tree_size(staged_appdir),
            "size_accounting": SIZE_ACCOUNTING,
            "runtime_inventory_sha256": _sha256(runtime_inventory),
            "appimagetool_sha256": _sha256(appimagetool),
            "appimagetool_commit": APPIMAGETOOL_COMMIT,
            "appimage_runtime_sha256": _sha256(appimage_runtime),
            "appimage_runtime_commit": APPIMAGE_RUNTIME_COMMIT,
            "acceleration": {
                "baseline": "cpu",
                "optional": ["cuda"],
                "packaged_backends": ["ctranslate2"],
                "gpu_driver": "host-provided",
            },
            "signed": False,
            "signature_scope": "NATIVE-16 release checksums/provenance",
            "system_integration": "none",
            "writable_roots": "XDG per-user roots only",
        }
        staged_evidence = build_root / evidence_path.name
        staged_evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        staged_checksum = build_root / checksum.name
        staged_checksum.write_text(
            f"{appimage_hash}  {appimage_name}\n", encoding="ascii"
        )

        # Publish only after every validation and metadata write succeeds. A failed tool
        # invocation therefore cannot strand output that blocks an identical retry.
        shutil.copytree(staged_appdir, appdir, symlinks=True)
        shutil.move(staged_appimage, appimage)
        shutil.move(staged_checksum, checksum)
        shutil.move(staged_evidence, evidence_path)
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    print(f"Linux AppImage written to {appimage}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Linux PyInstaller onedir")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--appimagetool", type=Path, required=True)
    parser.add_argument("--appimage-runtime", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        return package_linux(_parse_args())
    except (OSError, PackageError, subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
