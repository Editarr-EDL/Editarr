"""Compile the macOS app icon from its Icon Composer document."""

import plistlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
ICON_SOURCE = _ROOT / "web" / "public" / "editarr-app-icon.png"
ICON_COMPOSITION = Path(__file__).with_name("macos") / "icon.json"
ICON_NAME = "Editarr"


class IconBuildError(RuntimeError):
    """The Apple toolchain or compiled app icon is unavailable."""


def build_icon(
    resources: Path,
    build_root: Path,
    minimum_macos: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, object]:
    """Compile the native macOS icon and its legacy rendition from the original artwork."""
    tool = run(["xcrun", "--find", "actool"], capture=True, check=False)
    if tool.returncode:
        raise IconBuildError("macOS icon compilation requires Xcode 26 or later; set DEVELOPER_DIR")
    document = build_root / f"{ICON_NAME}.icon"
    assets = document / "Assets"
    assets.mkdir(parents=True)
    shutil.copyfile(ICON_COMPOSITION, document / "icon.json")
    run(["sips", "-z", "1024", "1024", str(ICON_SOURCE), "--out", str(assets / "Editarr.png")])
    metadata = build_root / "icon-info.plist"
    run(
        [
            "xcrun",
            "actool",
            "--compile",
            str(resources),
            "--platform",
            "macosx",
            "--minimum-deployment-target",
            minimum_macos,
            "--app-icon",
            ICON_NAME,
            "--output-partial-info-plist",
            str(metadata),
            str(document),
        ]
    )
    try:
        with metadata.open("rb") as source:
            return plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IconBuildError("macOS app icon metadata is invalid") from error
