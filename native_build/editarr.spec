# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir specification shared by Editarr's native target packages.

The release wrapper supplies only target-native helper paths and a validated staged
frontend.  This spec deliberately emits an onedir runtime; application-bundle wrapping,
signing, installers, and publishing are later platform tasks.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

from native_build.pyinstaller_collect import (
    FRONTEND,
    MIGRATIONS,
    MODEL_MANIFEST,
    ROOT,
    collected_packages,
    excluded_modules,
    target_id,
)


def _required_environment(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required native-build input {name}")
    path = Path(value).resolve()
    if not path.is_file():
        raise SystemExit(f"Native-build input {name} is not a file")
    return path


TARGET = os.environ.get("EDITARR_PYINSTALLER_TARGET") or target_id()
if TARGET != target_id():
    raise SystemExit("PyInstaller runtime targets must be built natively")
if not FRONTEND.is_dir():
    raise SystemExit("Validated frontend staging is required before freezing")
if not MODEL_MANIFEST.is_file():
    raise SystemExit("The model artifact manifest is required in the frozen runtime")

FFMPEG = _required_environment("EDITARR_PYINSTALLER_FFMPEG")
FFPROBE = _required_environment("EDITARR_PYINSTALLER_FFPROBE")
BACKUP_AGE = _required_environment("EDITARR_PYINSTALLER_BACKUP_AGE")
package_datas, package_binaries, hiddenimports = collected_packages(TARGET)

datas = [
    (str(FRONTEND), "scanner/frontend"),
    (str(MIGRATIONS), "scanner/persistence/migrations"),
    (str(ROOT / "scanner" / "backup" / "age_passphrase.exp"), "scanner/backup"),
    (str(MODEL_MANIFEST), "scanner"),
    (str(FFMPEG), "helpers"),
    (str(FFPROBE), "helpers"),
    (str(BACKUP_AGE), "helpers"),
]
if os.environ.get("EDITARR_PYINSTALLER_TEST_FAKE_MODELS") == "1":
    marker = ROOT / "native_build" / "native-test-fake-models.marker"
    datas.append((str(marker), "."))
if os.environ.get("EDITARR_PYINSTALLER_MACOS_RELEASE") == "1":
    marker = ROOT / "native_build" / "macos" / "macos-release.marker"
    datas.append((str(marker), "."))
# certifi's CA store is accessed by package code rather than a stable import path.
datas.extend(collect_data_files("certifi", includes=["cacert.pem"]))
datas.extend(package_datas)

a = Analysis(
    [str(ROOT / "scanner" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=package_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "native_build" / "hooks")],
    runtime_hooks=[str(ROOT / "native_build" / "hooks" / "pyi_rth_editarr_ffmpeg.py")],
    excludes=list(excluded_modules(TARGET)),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Editarr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Editarr",
)
