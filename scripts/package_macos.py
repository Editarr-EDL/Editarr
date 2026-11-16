#!/usr/bin/env python3
"""Wrap a verified Editarr runtime in an unsigned, self-signable macOS app and DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from native_build.artifact_size import SIZE_ACCOUNTING
from native_build.artifact_size import tree_size as _tree_size

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SOURCE = ROOT / "native_build" / "macos" / "EditarrLauncher.swift"
ICON_SOURCE = ROOT / "web" / "public" / "editarr-app-icon.png"
BUNDLE_IDENTIFIER = "org.editarr.app"
TARGET = "macos-arm64"
APP_NAME = "Editarr.app"
DMG_NAME = "Editarr.dmg"
INVENTORY_NAME = "editarr-macos-package.json"
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
_BUILD_VERSION_MINIMUM = re.compile(r"\bminos\s+([0-9]+(?:\.[0-9]+){1,2})")
_LEGACY_VERSION_MINIMUM = re.compile(r"\bversion\s+([0-9]+(?:\.[0-9]+){1,2})")
_LOAD_COMMAND_BLOCK = re.compile(
    r"Load command \d+\n(?:\s+[^\n]*\n)+?(?=Load command \d+\n|\Z)"
)


class PackageError(RuntimeError):
    """A macOS package input or verification is unsafe or incomplete."""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
    )


def _require_commands(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise PackageError("missing macOS packaging commands: " + ", ".join(missing))


def _project_version() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata["project"]["version"])
    if _VERSION.fullmatch(version) is None:
        raise PackageError("project version is not valid macOS bundle metadata")
    return version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(f"{path.stat().st_mode & 0o777:o}".encode())
            digest.update(bytes.fromhex(_sha256(path)))
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
    executable = runtime / "Editarr"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise PackageError("runtime does not contain an executable Editarr entry point")
    return inventory


def _require_distributable_runtime(runtime: Path, *, test_fake_models: bool) -> None:
    marker = runtime / "_internal" / "native-test-fake-models.marker"
    if marker.is_file() and not test_fake_models:
        raise PackageError(
            "a fake-model runtime is test-only; pass --test-fake-models only for disposable "
            "NATIVE-11 verification"
        )


def _version_key(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _macho_record(path: Path, *, root: Path) -> dict[str, Any] | None:
    description = _run(["file", "-b", str(path)], capture=True).stdout.strip()
    if "Mach-O" not in description:
        return None
    architectures = _run(["lipo", "-archs", str(path)], capture=True).stdout.split()
    load_commands = _run(["otool", "-l", str(path)], capture=True).stdout
    # Retain the exact relevant command stanza as build evidence.  The selected
    # product floor is deliberately not implied by this packaging spike: a
    # dependency can have been built with a newer SDK than the application
    # actually requires.  Keeping the raw, bounded stanza makes that distinction
    # auditable when dependencies are rebuilt for an older target.
    deployment_commands = [
        block.strip()
        for block in _LOAD_COMMAND_BLOCK.findall(load_commands)
        if "LC_BUILD_VERSION" in block or "LC_VERSION_MIN_MACOSX" in block
    ]
    minimums = [
        minimum
        for command in deployment_commands
        for minimum in (
            _BUILD_VERSION_MINIMUM.findall(command)
            if "LC_BUILD_VERSION" in command
            else _LEGACY_VERSION_MINIMUM.findall(command)
        )
    ]
    if not minimums:
        raise PackageError(f"Mach-O object has no macOS deployment load command: {path}")
    return {
        "path": path.relative_to(root).as_posix(),
        "architectures": sorted(set(architectures)),
        "minimum_macos": max(minimums, key=_version_key),
        "deployment_load_commands": deployment_commands,
    }


def _runtime_macho_records(runtime: Path) -> list[dict[str, Any]]:
    records = [
        record
        for path in sorted(runtime.rglob("*"))
        if path.is_file() and not path.is_symlink()
        for record in [_macho_record(path, root=runtime)]
        if record is not None
    ]
    if not records:
        raise PackageError("runtime contains no Mach-O code")
    incompatible = [
        record["path"] for record in records if record["architectures"] != ["arm64"]
    ]
    if incompatible:
        raise PackageError("runtime contains non-arm64-only code: " + ", ".join(incompatible[:8]))
    return records


def _minimum_macos(records: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    minimum = max((str(record["minimum_macos"]) for record in records), key=_version_key)
    forcing = [
        {
            "path": str(record["path"]),
            "minimum_macos": str(record["minimum_macos"]),
            "deployment_load_commands": record["deployment_load_commands"],
        }
        for record in records
        if _version_key(str(record["minimum_macos"])) == _version_key(minimum)
    ]
    forcing.sort(key=lambda record: str(record["path"]))
    return minimum, forcing


def _build_icon(destination: Path, build_root: Path) -> None:
    iconset = build_root / "Editarr.iconset"
    iconset.mkdir()
    for points, pixels in ((16, 16), (16, 32), (32, 32), (32, 64), (128, 128), (128, 256),
                           (256, 256), (256, 512), (512, 512), (512, 1024)):
        suffix = "@2x" if pixels == points * 2 else ""
        output = iconset / f"icon_{points}x{points}{suffix}.png"
        _run(["sips", "-z", str(pixels), str(pixels), str(ICON_SOURCE), "--out", str(output)])
    _run(["iconutil", "-c", "icns", str(iconset), "-o", str(destination)])


def _build_launcher(destination: Path, minimum_macos: str) -> None:
    _run(
        [
            "xcrun",
            "swiftc",
            "-O",
            "-swift-version",
            "5",
            "-target",
            f"arm64-apple-macos{minimum_macos}",
            "-framework",
            "AppKit",
            "-framework",
            "ServiceManagement",
            str(LAUNCHER_SOURCE),
            "-o",
            str(destination),
        ]
    )
    destination.chmod(0o755)


def _write_info_plist(path: Path, *, version: str, minimum_macos: str) -> None:
    values: dict[str, object] = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Editarr",
        "CFBundleExecutable": "Editarr",
        "CFBundleIconFile": "Editarr.icns",
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Editarr",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleVersion": version,
        "LSArchitecturePriority": ["arm64"],
        "LSMinimumSystemVersion": minimum_macos,
        "LSRequiresNativeExecution": True,
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    with path.open("wb") as output:
        plistlib.dump(values, output, sort_keys=True)


def _end_user_self_sign_commands(app: str = "/Applications/Editarr.app") -> list[list[str]]:
    """Return the documented, local-only activation sequence for this release policy."""

    return [
        ["xattr", "-dr", "com.apple.quarantine", app],
        ["codesign", "--force", "--deep", "--sign", "-", app],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app],
    ]


def _assert_end_user_self_sign_required(app: Path) -> None:
    """Fail closed if a future build accidentally makes the shipped bundle launch-ready."""

    result = _run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        capture=True,
        check=False,
    )
    if result.returncode == 0:
        raise PackageError(
            "refusing a macOS package that already passes strict signature verification; "
            "the current distribution policy requires end-user local self-signing"
        )


def _validate_app_layout(app: Path, *, minimum_macos: str) -> None:
    """Check the bundle contract before it is sealed into a DMG."""

    contents = app / "Contents"
    plist_path = contents / "Info.plist"
    try:
        with plist_path.open("rb") as source:
            plist = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise PackageError("macOS application Info.plist is invalid") from error
    expected = {
        "CFBundleExecutable": "Editarr",
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": minimum_macos,
        "LSUIElement": True,
        "LSRequiresNativeExecution": True,
    }
    for key, value in expected.items():
        if plist.get(key) != value:
            raise PackageError(f"macOS application Info.plist has an invalid {key}")
    launcher = contents / "MacOS" / "Editarr"
    runtime = contents / "Resources" / "runtime" / "Editarr" / "Editarr"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise PackageError("macOS application launcher is missing or not executable")
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise PackageError("macOS application embedded runtime is missing or not executable")
    if not (contents / "Resources" / "Editarr.icns").is_file():
        raise PackageError("macOS application icon is missing")


def _create_dmg(app: Path, destination: Path, build_root: Path) -> None:
    staging = build_root / "dmg"
    staging.mkdir()
    shutil.move(str(app), staging / APP_NAME)
    os.symlink("/Applications", staging / "Applications")
    _run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Editarr",
            "-srcfolder",
            str(staging),
            "-format",
            "UDZO",
            "-ov",
            str(destination),
        ]
    )
    shutil.move(str(staging / APP_NAME), app)
    _run(["hdiutil", "verify", str(destination)])


def package(args: argparse.Namespace) -> int:
    _require_commands(
        "codesign", "file", "hdiutil", "iconutil", "lipo", "otool", "sips", "swiftc", "xcrun"
    )
    source_runtime = args.runtime.resolve()
    _read_runtime_inventory(source_runtime)
    _require_distributable_runtime(source_runtime, test_fake_models=args.test_fake_models)

    output = args.out_dir.resolve()
    app_output = output / APP_NAME
    dmg_output = output / DMG_NAME
    inventory_output = output / INVENTORY_NAME
    if any(path.exists() for path in (app_output, dmg_output, inventory_output)):
        raise PackageError("refusing to overwrite an existing macOS package output")
    output.mkdir(parents=True, exist_ok=True)
    build_root = Path(
        tempfile.mkdtemp(prefix="editarr-macos-package-", dir=args.work_dir.resolve())
    )
    app = build_root / APP_NAME
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    embedded_runtime = resources / "runtime" / "Editarr"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    try:
        shutil.copytree(source_runtime, embedded_runtime, symlinks=True)
        notices = getattr(args, "notices", None)
        if (source_runtime / "_internal" / "macos-release.marker").is_file():
            if notices is None or not (notices / "THIRD-PARTY-NOTICES.md").is_file():
                raise PackageError("a macOS release runtime requires the reviewed license notices")
        if notices is not None:
            shutil.copytree(notices, resources / "Licenses")
        records = _runtime_macho_records(embedded_runtime)
        minimum_macos, forcing_dependencies = _minimum_macos(records)
        _build_launcher(macos / "Editarr", minimum_macos)
        _build_icon(resources / "Editarr.icns", build_root)
        _write_info_plist(
            contents / "Info.plist", version=_project_version(), minimum_macos=minimum_macos
        )
        (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
        _validate_app_layout(app, minimum_macos=minimum_macos)
        _assert_end_user_self_sign_required(app)
        _create_dmg(app, dmg_output, build_root)

        shutil.move(str(app), app_output)

        package_inventory = {
            "format": 1,
            "app": APP_NAME,
            "app_sha256": _tree_hash(app_output),
            "app_size_bytes": _tree_size(app_output),
            "size_accounting": SIZE_ACCOUNTING,
            "architecture": "arm64",
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "dmg": DMG_NAME,
            "dmg_sha256": _sha256(dmg_output),
            "dmg_size_bytes": dmg_output.stat().st_size,
            "minimum_macos": minimum_macos,
            "minimum_macos_basis": {
                "kind": "provisional-packaged-load-command-maximum",
                "forcing_objects": forcing_dependencies,
                "lowering_path": (
                    "Rebuild or replace the listed third-party Mach-O objects with an "
                    "older deployment target, then repeat the real-model package smoke; "
                    "do not treat this build-time maximum as an approved product floor."
                ),
            },
            "runtime_inventory_sha256": _sha256(
                app_output
                / "Contents"
                / "Resources"
                / "runtime"
                / "Editarr"
                / "editarr-runtime-inventory.json"
            ),
            "signing": {
                "distribution": "unsigned-end-user-self-sign-required",
                "notarization": "not-applicable-under-current-policy",
                "gatekeeper": "not-assessed-under-current-policy",
                "end_user_commands": _end_user_self_sign_commands(),
            },
            "target": TARGET,
            "version": _project_version(),
        }
        inventory_output.write_text(
            json.dumps(package_inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    print(f"macOS application written to {app_output}")
    print(f"macOS disk image written to {dmg_output}")
    return 0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="NATIVE-10 macOS onedir root")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--notices", type=Path, help="License files to include in the app")
    parser.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument(
        "--test-fake-models",
        action="store_true",
        help="permit a disposable marker-gated NATIVE-11 verification package; never distribute it",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return package(_arguments())
    except (PackageError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
