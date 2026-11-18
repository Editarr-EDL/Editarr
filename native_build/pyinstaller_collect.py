"""Deliberate PyInstaller collection policy for the Editarr native runtime.

The spec imports this small module rather than spreading hidden-import and resource
policy over platform packaging files.  It does not inspect user runtime paths and it
never follows model-cache, development-data, or source-prototype directories.
"""

from __future__ import annotations

import os
import platform
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Final

from PyInstaller.utils.hooks import collect_all

ROOT: Final = Path(__file__).resolve().parent.parent
FRONTEND: Final = ROOT / "scanner" / "frontend"
MODEL_MANIFEST: Final = ROOT / "scanner" / "model-manifest-v1.json"
MIGRATIONS: Final = ROOT / "scanner" / "persistence" / "migrations"

# Dynamic imports in pipeline modules and packages whose data/native libraries are
# loaded after startup.  These are package names, not source paths; they are stable
# inputs for target packagers.
BASE_PACKAGES: Final = (
    "alembic",
    "av",
    "better_profanity",
    "certifi",
    "cryptography",
    "ctranslate2",
    "cv2",
    "faster_whisper",
    "glin_profanity",
    "huggingface_hub",
    "imageio_ffmpeg",
    "nudenet",
    "numpy",
    "onnxruntime",
    "PIL",
    "profanity_check",
    "safetensors",
    "scenedetect",
    "sqlalchemy",
    "timm",
    "torch",
    "torchvision",
    "transformers",
    "uvicorn",
)
MACOS_MLX_PACKAGES: Final = ("mlx", "mlx_vlm", "mlx_whisper")
# CTranslate2's target-native wheel deliberately supplies its own optional CUDA runtime
# binaries.  These exclusions still prevent unrelated MLX, generic CUDA Python packages,
# and Torch CUDA stacks from being pulled into Windows/Linux artifacts accidentally.
NON_MACOS_EXCLUDES: Final = (
    "mlx",
    "mlx_whisper",
    "mlx_vlm",
    "mlx_lm",
    "nvidia",
    "cuda",
    "torch.cuda",
)
_DISTRIBUTION_NAMES: Final = {
    "PIL": "Pillow",
    "cv2": "opencv-python-headless",
    "faster_whisper": "faster-whisper",
    "glin_profanity": "glin-profanity",
    "imageio_ffmpeg": "imageio-ffmpeg",
    "mlx_vlm": "mlx-vlm",
    "mlx_whisper": "mlx-whisper",
    "profanity_check": "alt-profanity-check",
}

# Prevent accidental collection of checkout state if future hook changes add a broad
# source tree.  Package-owned configuration data remains permitted; only known
# application-managed/local paths are rejected.
FORBIDDEN_SOURCE_PARTS: Final = frozenset(
    {
        ".cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".venv",
        "data",
        "corpora",
        "models",
        "credentials",
        "samwisenative",
    }
)
_EXCLUDED_PACKAGE_DATA_PARTS: Final = frozenset({"test", "tests", "corpora"})


def target_id(*, system: str | None = None, machine: str | None = None) -> str:
    """Return the release target represented by this native build host."""

    current_system = (system or platform.system()).lower()
    current_machine = (machine or platform.machine()).lower()
    targets = {
        ("darwin", "arm64"): "macos-arm64",
        ("darwin", "aarch64"): "macos-arm64",
        ("windows", "amd64"): "windows-x64",
        ("windows", "x86_64"): "windows-x64",
        ("linux", "x86_64"): "linux-x86_64",
        ("linux", "amd64"): "linux-x86_64",
    }
    try:
        return targets[(current_system, current_machine)]
    except KeyError as error:
        raise RuntimeError(
            "Editarr native runtimes must be built on their declared target host; "
            f"unsupported host is {current_system}-{current_machine}"
        ) from error


def is_macos_target(target: str) -> bool:
    return target == "macos-arm64"


def required_packages(target: str) -> tuple[str, ...]:
    """Return package families intentionally present for one target."""

    if target not in {"macos-arm64", "windows-x64", "linux-x86_64"}:
        raise ValueError(f"Unsupported Editarr runtime target: {target}")
    return BASE_PACKAGES + (MACOS_MLX_PACKAGES if is_macos_target(target) else ())


def excluded_modules(target: str) -> tuple[str, ...]:
    """Exclude unrelated accelerator stacks while retaining CTranslate2 CUDA support."""

    return () if is_macos_target(target) else NON_MACOS_EXCLUDES


def _is_forbidden_source(source: str | os.PathLike[str]) -> bool:
    source_path = Path(source).resolve()
    parts = {part.lower() for part in source_path.parts}
    if parts.intersection(_EXCLUDED_PACKAGE_DATA_PARTS):
        return True
    try:
        relative = source_path.relative_to(ROOT)
    except ValueError:
        return False
    # The virtual environment normally lives inside the checkout. Its installed package
    # data is a required collection source, whereas only top-level application state is
    # forbidden. Do not mistake a dependency's own ``models`` module for model weights.
    return bool(relative.parts) and relative.parts[0].lower() in FORBIDDEN_SOURCE_PARTS - {
        ".venv",
        "models",
    }


def _filtered(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [item for item in items if not _is_forbidden_source(item[0])]


def _runtime_submodule(name: str) -> bool:
    """Avoid discovering test suites and their optional development dependencies."""
    return not _EXCLUDED_PACKAGE_DATA_PARTS.intersection(name.split("."))


def _data_exclusions(package: str) -> list[str]:
    """Exclude build-only Torch resources and the separately inventoried helper."""
    if package == "torch":
        return ["include/**", "bin/protoc*"]
    if package == "imageio_ffmpeg":
        return ["binaries/ffmpeg-*"]
    return []


def collected_packages(
    target: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Collect code data, native libraries, and hidden imports for the target only."""

    datas: list[tuple[str, str]] = []
    binaries: list[tuple[str, str]] = []
    hiddenimports: list[str] = []
    for package in required_packages(target):
        package_datas, package_binaries, package_hiddenimports = collect_all(
            package,
            filter_submodules=_runtime_submodule,
            exclude_datas=_data_exclusions(package),
        )
        datas.extend(package_datas)
        binaries.extend(package_binaries)
        hiddenimports.extend(package_hiddenimports)
    return _filtered(datas), _filtered(binaries), sorted(set(hiddenimports))


def package_versions(target: str) -> dict[str, str]:
    """Record distribution versions for inventory without importing model packages."""

    versions: dict[str, str] = {}
    for package in required_packages(target):
        try:
            metadata = distribution(_DISTRIBUTION_NAMES.get(package, package))
        except PackageNotFoundError as error:
            raise RuntimeError(
                f"Required {target} native package is unavailable: {package}"
            ) from error
        versions[metadata.metadata["Name"]] = metadata.version
    return dict(sorted(versions.items()))
