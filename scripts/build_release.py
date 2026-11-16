#!/usr/bin/env python3
"""Build a reproducible, onedir Editarr native runtime.

The legacy wheel/sdist builder remains available behind ``--wheel`` for internal
development. Native output is built only on its matching target host and never leaves
frontend staging in the checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Iterator

from native_build.artifact_size import tree_size as _tree_size
from native_build.artifact_size import write_sized_inventory
from native_build.runtime_content import (
    DEVELOPMENT_OUTPUT_PARTS,
    MANAGED_OUTPUT_NAMES,
)
from native_build.runtime_content import assert_clean_runtime as _assert_clean_runtime
from native_build.runtime_content import (
    prune_excluded_runtime_content as _prune_excluded_runtime_content,
)
from native_build.runtime_layout import helper_name, resolve_executable

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
STAGED_FRONTEND = ROOT / "scanner" / "frontend"
SPEC = ROOT / "native_build" / "editarr.spec"
DEFAULT_SOURCE_DATE_EPOCH = "315532800"


def _requires_windows_shell(
    command: list[str], *, platform_name: str | None = None
) -> bool:
    """Allow the Windows command processor only for trusted batch tool shims."""

    name = os.name if platform_name is None else platform_name
    resolved = shutil.which(command[0])
    return bool(
        name == "nt"
        and resolved
        and Path(resolved).suffix.lower() in {".bat", ".cmd"}
    )


def _run(command: list[str], *, cwd: Path, environment: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        shell=_requires_windows_shell(command),
    )


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_collected_frontend(runtime: Path, expected_hashes: dict[str, str]) -> None:
    """Reject a runtime unless PyInstaller collected the exact staged UI.

    The inventory records the build input hashes, but that is only meaningful when
    those files survived collection into the onedir runtime.  Keep this assertion
    before writing the inventory so a partial build cannot look release-ready.
    """
    collected = runtime / "_internal" / "scanner" / "frontend"
    if not collected.is_dir():
        raise RuntimeError("PyInstaller did not collect the staged frontend")
    actual_hashes = _file_hashes(collected)
    if actual_hashes != expected_hashes:
        raise RuntimeError("PyInstaller collected frontend assets do not match staged frontend")


def _assert_wheel_backup_helper(wheel: Path) -> None:
    """Fail packaging when the optional age prompt helper was omitted from the wheel."""
    if not wheel.is_file():
        raise RuntimeError("Wheel build did not produce the expected artifact")
    with zipfile.ZipFile(wheel) as artifact:
        if "scanner/backup/age_passphrase.exp" not in artifact.namelist():
            raise RuntimeError("Wheel omitted the age passphrase helper")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_version() -> str:
    values = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(values["project"]["version"])


def _git_revision() -> str | None:
    from scripts.release_assets import source_identity

    revision, _dirty = source_identity(ROOT)
    return revision


def _require_command(command: str, purpose: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"{command} is required to {purpose}")


def _target_inputs() -> tuple[str, dict[str, str]]:
    from native_build.pyinstaller_collect import package_versions, target_id

    target = target_id()
    return target, package_versions(target)


def _imageio_ffmpeg() -> Path:
    import imageio_ffmpeg

    helper = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not helper.is_file():
        raise RuntimeError("imageio-ffmpeg did not resolve a bundled FFmpeg helper")
    return helper


def _target_helper(value: str | None, name: str) -> Path:
    if not value:
        raise RuntimeError(
            f"A relocatable {name} helper is required. Pass --{name}-helper from the "
            "target release toolchain; do not use an untracked system PATH dependency."
        )
    supplied = Path(value).expanduser()
    helper = supplied.resolve()
    if supplied.is_symlink() or not helper.is_file():
        raise RuntimeError(f"--{name}-helper must name a regular target helper executable")
    return helper


def _ffprobe_helper(value: str | None) -> Path:
    return _target_helper(value, "ffprobe")


def _stage_helpers(
    helpers: dict[str, Path], build_root: Path, *, target: str
) -> dict[str, Path]:
    """Give collected helpers stable runtime filenames independent of wheel filenames."""

    destination = build_root / "helpers"
    destination.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    for name, source in helpers.items():
        staged_path = destination / helper_name(name, target)
        shutil.copy2(source, staged_path)
        staged_path.chmod(source.stat().st_mode)
        staged[name] = staged_path
    return staged


def _build_backup_age(build_root: Path, target: str) -> Path:
    """Build the pinned, native age sidecar without changing the checkout."""
    _require_command("go", "build the encrypted backup helper")
    from native_build.runtime_layout import helper_name

    result = build_root / helper_name("backup-age", target)
    _run(
        ["go", "build", "-mod=readonly", "-trimpath", "-o", str(result), "."],
        cwd=ROOT / "native_build" / "backup_age",
    )
    return result


def _validate_pnpm() -> None:
    expected = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["packageManager"]
    actual = subprocess.run(
        ["pnpm", "--version"], cwd=WEB, check=True, capture_output=True, text=True
    ).stdout.strip()
    if expected != f"pnpm@{actual}":
        raise RuntimeError(f"release builds require {expected}; found pnpm@{actual}")


@contextmanager
def _staged_frontend() -> Iterator[dict[str, str]]:
    """Build, validate, and always remove package-local frontend staging."""

    if STAGED_FRONTEND.exists():
        raise RuntimeError(f"release staging already exists at {STAGED_FRONTEND}")
    from scripts.release_assets import validate_frontend

    shutil.rmtree(DIST, ignore_errors=True)
    try:
        _run(["pnpm", "install", "--frozen-lockfile"], cwd=WEB)
        _run(["pnpm", "build"], cwd=WEB)
        validate_frontend(DIST)
        shutil.copytree(DIST, STAGED_FRONTEND)
        validate_frontend(STAGED_FRONTEND)
        yield _file_hashes(STAGED_FRONTEND)
    finally:
        shutil.rmtree(STAGED_FRONTEND, ignore_errors=True)


def _inventory(
    *,
    runtime: Path,
    target: str,
    frontend_hashes: dict[str, str],
    packages: dict[str, str],
    helpers: dict[str, Path],
) -> dict[str, object]:
    from scripts.release_assets import source_identity

    if set(helpers) != {"ffmpeg", "ffprobe", "backup-age"}:
        raise RuntimeError("native runtime requires ffmpeg, ffprobe and backup-age helpers")
    _revision, dirty = source_identity(ROOT)
    # ``sys._MEIPASS`` is the onedir ``_internal`` directory.  Hash the collected
    # helper rather than its build-time source: macOS code signing can legitimately
    # alter the final Mach-O bytes.
    collected_helpers = runtime / "_internal" / "helpers"
    helper_inventory: dict[str, dict[str, object]] = {}
    for name in sorted(helpers):
        collected = collected_helpers / helper_name(name, target)
        if not collected.is_file():
            raise RuntimeError(f"PyInstaller did not collect required helper {name}")
        helper_inventory[name] = {
            "path": collected.relative_to(runtime).as_posix(),
            "sha256": _sha256(collected),
            "size_bytes": collected.stat().st_size,
        }
    return {
        "format": 1,
        "version": _project_version(),
        "target": target,
        "source_revision": _git_revision(),
        "source_dirty": dirty,
        "frontend_sha256": dict(sorted(frontend_hashes.items())),
        "packages": packages,
        "resources_root": "_internal",
        "helpers": helper_inventory,
        "exclusions": {
            "managed_paths": sorted(MANAGED_OUTPUT_NAMES),
            "development_modules": sorted(DEVELOPMENT_OUTPUT_PARTS),
            "model_weights": "on-demand managed cache only",
            "retired_prototype": "native/SamwiseNative",
            "target_specific": (
                "macOS arm64 includes MLX packages"
                if target == "macos-arm64"
                else (
                    "MLX and unrelated GPU stacks excluded; CPU baseline with optional "
                    "CTranslate2 CUDA runtime"
                )
            ),
        },
        "acceleration": (
            {
                "baseline": "cpu",
                "optional": ["cuda"],
                "packaged_backends": ["ctranslate2"],
                "gpu_driver": "host-provided",
            }
            if target in {"windows-x64", "linux-x86_64"}
            else {"baseline": "cpu", "optional": ["coreml", "metal", "mlx"]}
        ),
        "runtime_size_bytes": _tree_size(runtime),
    }


def _write_inventory(runtime: Path, inventory: dict[str, object]) -> Path:
    return write_sized_inventory(runtime / "editarr-runtime-inventory.json", inventory)


def _frozen_smoke_environment(temporary_root: Path, target: str) -> dict[str, str]:
    environment: dict[str, str] = {
        "EDITARR_FROZEN_RUNTIME_SMOKE": "1",
        "EDITARR_DATA_DIR": str(temporary_root / "data"),
        "HOME": str(temporary_root / "home"),
        "XDG_CACHE_HOME": str(temporary_root / "cache"),
        "XDG_CONFIG_HOME": str(temporary_root / "config"),
        "XDG_STATE_HOME": str(temporary_root / "state"),
        "TMPDIR": str(temporary_root / "tmp"),
    }
    if target == "windows-x64":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        system32 = ntpath.join(system_root, "System32")
        environment.update(
            {
                "USERPROFILE": str(temporary_root / "home"),
                "LOCALAPPDATA": str(temporary_root / "local-app-data"),
                "TEMP": str(temporary_root / "tmp"),
                "TMP": str(temporary_root / "tmp"),
                "SystemRoot": system_root,
                "WINDIR": os.environ.get("WINDIR", system_root),
                "COMSPEC": os.environ.get("COMSPEC", ntpath.join(system32, "cmd.exe")),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                # System32 is enough for ordinary OS helpers, but cannot borrow
                # Python, Node, pnpm, a checkout, or a developer toolchain.
                "PATH": system32,
            }
        )
    else:
        # No checkout, venv, Node, or Homebrew lookup may rescue the runtime.
        environment["PATH"] = "/usr/bin:/bin"
    return environment


def _run_frozen_smoke(executable: Path, *, temporary_root: Path, target: str) -> None:
    for directory in (
        temporary_root,
        temporary_root / "home",
        temporary_root / "tmp",
        temporary_root / "local-app-data",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    environment = _frozen_smoke_environment(temporary_root, target)
    _run([str(executable)], cwd=temporary_root, environment=environment)


def _smoke_backup_helper(runtime: Path, *, temporary_root: Path, target: str) -> None:
    """Round-trip a dummy payload with the collected binary, without host age tools."""
    from scanner.backup.age_transport import validate_age_header

    helper = runtime / "_internal" / "helpers" / helper_name("backup-age", target)
    temporary_root.mkdir(parents=True, exist_ok=True)
    source = temporary_root / "source"
    encrypted = temporary_root / "encrypted"
    restored = temporary_root / "restored"
    source.write_bytes(b"native backup helper smoke")
    environment = _frozen_smoke_environment(temporary_root, target)
    for operation, input_path, output_path in (
        ("encrypt", source, encrypted),
        ("decrypt", encrypted, restored),
    ):
        subprocess.run(
            [str(helper), operation, str(input_path), str(output_path)],
            input=b"disposable-test-passphrase",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=70,
            check=True,
        )
    validate_age_header(encrypted)
    if restored.read_bytes() != source.read_bytes():
        raise RuntimeError("Collected backup helper failed its round trip")


def build_native(args: argparse.Namespace) -> int:
    from scripts.release_assets import validate_release_versions

    validate_release_versions(ROOT)
    _require_command("pnpm", "build the locked frontend")
    _require_command("uv", "run the locked PyInstaller build")
    target, packages = _target_inputs()
    if args.target and args.target != target:
        raise RuntimeError(f"target {args.target} must be built natively as {target}")
    if args.macos_release and (target != "macos-arm64" or args.test_fake_models):
        raise RuntimeError("--macos-release requires a real macOS arm64 runtime")
    ffmpeg = (
        _target_helper(args.ffmpeg_helper, "ffmpeg") if args.macos_release else _imageio_ffmpeg()
    )
    ffprobe = _ffprobe_helper(args.ffprobe_helper)
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / "Editarr"
    if runtime.exists():
        raise RuntimeError(f"refusing to overwrite existing native runtime {runtime}")
    work_parent = args.work_dir.resolve()
    work_parent.mkdir(parents=True, exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix="editarr-pyinstaller-", dir=work_parent))
    backup_age = _build_backup_age(build_root, target)
    helpers = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "backup-age": backup_age}
    staged_helpers = _stage_helpers(helpers, build_root, target=target)
    environment = os.environ.copy()
    environment.setdefault("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH)
    environment.update(
        {
            "EDITARR_PYINSTALLER_TARGET": target,
            "EDITARR_PYINSTALLER_FFMPEG": str(staged_helpers["ffmpeg"]),
            "EDITARR_PYINSTALLER_FFPROBE": str(staged_helpers["ffprobe"]),
            "EDITARR_PYINSTALLER_BACKUP_AGE": str(staged_helpers["backup-age"]),
            # PyInstaller otherwise persists its discovery cache below the developer's
            # home directory. Build-only state must stay inside our disposable root.
            "PYINSTALLER_CONFIG_DIR": str(build_root / "pyinstaller-config"),
        }
    )
    if args.test_fake_models:
        # The spec collects an immutable marker into this explicitly non-release
        # artifact. Runtime code still requires both this marker and the matching
        # environment value before it can take the fake-adapter branch.
        environment["EDITARR_PYINSTALLER_TEST_FAKE_MODELS"] = "1"
    if args.macos_release:
        environment["EDITARR_PYINSTALLER_MACOS_RELEASE"] = "1"
    try:
        with _staged_frontend() as frontend_hashes:
            _run(
                [
                    "uv",
                    "run",
                    "pyinstaller",
                    "--noconfirm",
                    "--clean",
                    "--distpath",
                    str(output),
                    "--workpath",
                    str(build_root / "work"),
                    str(SPEC),
                ],
                cwd=ROOT,
                environment=environment,
            )
            _prune_excluded_runtime_content(runtime)
            _assert_clean_runtime(runtime)
            _assert_collected_frontend(runtime, frontend_hashes)
            _smoke_backup_helper(
                runtime, temporary_root=build_root / "backup-smoke", target=target
            )
            _run_frozen_smoke(
                resolve_executable(runtime, target),
                temporary_root=build_root / "smoke",
                target=target,
            )
            _write_inventory(
                runtime,
                _inventory(
                    runtime=runtime,
                    target=target,
                    frontend_hashes=frontend_hashes,
                    packages=packages,
                    helpers=helpers,
                ),
            )
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    print(f"Native {target} runtime written to {runtime}")
    return 0


def build_wheel(args: argparse.Namespace) -> int:
    from scripts.release_assets import validate_release_versions

    validate_release_versions(ROOT)
    _require_command("pnpm", "build the locked frontend")
    _require_command("uv", "build wheel/sdist artifacts")
    _validate_pnpm()
    output = args.out_dir.resolve()
    with _staged_frontend() as hashes:
        environment = os.environ.copy()
        environment.setdefault("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH)
        output.mkdir(parents=True, exist_ok=True)
        # Build both from the checkout so the wheel records the source commit.
        _run(
            ["uv", "build", "--sdist", "--wheel", "--out-dir", str(output)],
            cwd=ROOT,
            environment=environment,
        )
        _assert_wheel_backup_helper(output / f"editarr-{_project_version()}-py3-none-any.whl")
    print(json.dumps({"frontend_sha256": hashes}, indent=2, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Editarr native runtime release inputs.")
    parser.add_argument("--wheel", action="store_true", help="Build internal wheel/sdist artifacts")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist", help="Output directory")
    parser.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--target", choices=("macos-arm64", "windows-x64", "linux-x86_64"))
    parser.add_argument("--ffprobe-helper", help="Regular target-native ffprobe helper to bundle")
    parser.add_argument("--ffmpeg-helper", help="Source-built FFmpeg for the macOS release")
    parser.add_argument("--macos-release", action="store_true", help="Use macOS release inputs")
    parser.add_argument(
        "--test-fake-models",
        action="store_true",
        help="Build a disposable NATIVE-11 test runtime with deterministic fake model adapters",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        return build_wheel(args) if args.wheel else build_native(args)
    except (PackageNotFoundError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
