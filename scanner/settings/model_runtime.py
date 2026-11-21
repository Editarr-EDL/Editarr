"""Offline package and device probes for the model runtime."""

from __future__ import annotations

import platform
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import Literal

PackageProbe = Callable[[str], bool]
VersionProbe = Callable[[str], "str | None"]


def installed_version(distribution: str) -> str | None:
    """Return a distribution's installed version from metadata, without importing it."""

    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def version_at_least(installed: str | None, minimum: tuple[int, ...]) -> bool:
    """Compare the leading numeric release parts of ``installed`` with ``minimum``."""

    if not installed:
        return False
    parts: list[int] = []
    for part in installed.split(".")[: len(minimum)]:
        digits = re.match(r"\d+", part)
        if digits is None:
            break
        parts.append(int(digits.group()))
    return tuple(parts) >= minimum


@dataclass(frozen=True)
class CapabilityEnvironment:
    """Injectable package and accelerator inventory for model capabilities."""

    system: str
    machine: str
    package_available: PackageProbe
    providers: frozenset[str]
    torch_devices: frozenset[str] = frozenset()
    ctranslate2_devices: frozenset[str] = frozenset()
    package_version: VersionProbe = installed_version

    @property
    def target_id(self) -> str:
        machine = self.machine.lower()
        if self.system == "Darwin" and machine in {"arm64", "aarch64"}:
            return "macos-arm64"
        if self.system == "Windows" and machine in {"amd64", "x86_64"}:
            return "windows-x64"
        if self.system == "Linux" and machine in {"x86_64", "amd64"}:
            return "linux-x86_64"
        return "unsupported"

    @property
    def onnx_device(self) -> Literal["coreml", "cpu"]:
        if self.target_id == "macos-arm64" and "CoreMLExecutionProvider" in self.providers:
            return "coreml"
        return "cpu"

    @property
    def torch_device(self) -> Literal["metal", "cpu"]:
        if self.target_id == "macos-arm64" and "mps" in self.torch_devices:
            return "metal"
        return "cpu"


@lru_cache(maxsize=None)
def _installed_package(name: str) -> bool:
    """Return package presence without importing it or executing package code."""

    try:
        return find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


@lru_cache(maxsize=2)
def _packaged_onnx_providers(macos_arm64: bool = False) -> frozenset[str]:
    """Discover Core ML in the native macOS wheel and retain the CPU baseline."""

    if not _installed_package("onnxruntime"):
        return frozenset()
    if macos_arm64:
        try:
            providers = import_module("onnxruntime").get_available_providers()
            allowed = {"CPUExecutionProvider", "CoreMLExecutionProvider"}
            return frozenset(allowed.intersection(providers))
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
    return frozenset({"CPUExecutionProvider"})


@lru_cache(maxsize=1)
def _verified_mps_devices() -> frozenset[str]:
    """Check that the packaged PyTorch can execute a small Metal operation."""

    if _installed_package("torch"):
        try:
            torch = import_module("torch")
            if torch.backends.mps.is_available():
                sample = torch.ones(1, device="mps")
                if (sample + sample).item() == 2:
                    return frozenset({"mps"})
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
    return frozenset()


@lru_cache(maxsize=1)
def _verified_ctranslate2_devices() -> frozenset[str]:
    """Return CUDA only after CTranslate2 proves a usable device and runtime.

    CTranslate2 wheels can contain CUDA-named libraries on machines that have no NVIDIA
    driver.  File or module presence is therefore not evidence.  These calls initialize
    no model and download nothing, but exercise the packaged native runtime against the
    host driver.  Any missing/incompatible driver or runtime fails closed to CPU.
    """

    if not _installed_package("ctranslate2"):
        return frozenset()
    try:
        ctranslate2 = import_module("ctranslate2")
        if int(ctranslate2.get_cuda_device_count()) < 1:
            return frozenset()
        if not ctranslate2.get_supported_compute_types("cuda"):
            return frozenset()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return frozenset()
    return frozenset({"cuda"})


def current_environment() -> CapabilityEnvironment:
    """Describe the current process without loading models or touching their caches."""

    environment = CapabilityEnvironment(
        system=platform.system(),
        machine=platform.machine(),
        package_available=_installed_package,
        providers=frozenset(),
    )
    target = environment.target_id
    macos_arm64 = target == "macos-arm64"
    return replace(
        environment,
        providers=_packaged_onnx_providers(macos_arm64),
        torch_devices=_verified_mps_devices() if macos_arm64 else frozenset(),
        ctranslate2_devices=(
            _verified_ctranslate2_devices()
            if target in {"windows-x64", "linux-x86_64"}
            else frozenset()
        ),
    )
