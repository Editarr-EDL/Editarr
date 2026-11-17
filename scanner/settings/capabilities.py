"""Fast, offline declarations of the model backends this runtime may start.

This module uses package metadata plus bounded, cached runtime probes.  It must stay safe
to call from the capabilities API and request validation: no model loads, cache creation,
or downloads belong here.  Packagers and tests may supply a precise dependency and
provider inventory through :class:`CapabilityEnvironment`.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from importlib.util import find_spec
from typing import Literal

from scanner.models import ModelCapability, ModelDownloadState

AvailabilityReason = Literal[
    "unsupported_platform",
    "unsupported_architecture",
    "missing_package",
    "missing_provider",
    "deferred_gap",
]

TARGET_IDS = ("macos-arm64", "windows-x64", "linux-x86_64")
DEFAULT_AUDIO_MODEL = "whisper"
DEFAULT_AUDIO_FILTER = "better_profanity"
DEFAULT_QWEN_MODEL = "qwen3-vl-8b-instruct-4bit"
SUPPORTED_AUDIO_MODELS = (DEFAULT_AUDIO_MODEL, "mlx_whisper")
SUPPORTED_AUDIO_FILTERS = (
    DEFAULT_AUDIO_FILTER,
    "glin_profanity",
    "alt_profanity_check",
    "qwen",
)
SUPPORTED_AUDIO_FILTER_SELECTIONS = SUPPORTED_AUDIO_FILTERS + ("alt-profanity-check",)
SUPPORTED_VIDEO_MODELS = (
    "nudenet",
    "image_safety_gate",
    "siglip2_moderation_nsfw",
    "siglip2_moderation_violence",
)
SUPPORTED_QWEN_MODELS = (DEFAULT_QWEN_MODEL, "qwen3-vl-4b-instruct-4bit")

DISPLAY_NAMES = {
    "whisper": "Whisper (faster-whisper)",
    "mlx_whisper": "Whisper (mlx-whisper)",
    "better_profanity": "better-profanity",
    "glin_profanity": "glin-profanity",
    "alt_profanity_check": "alt-profanity-check",
    "qwen": "Qwen (multilingual)",
    "nudenet": "NudeNet",
    "image_safety_gate": "Image Safety Gate",
    "siglip2_moderation_nsfw": "SigLIP2 Moderation — NSFW",
    "siglip2_moderation_violence": "SigLIP2 Moderation — Violence",
    "qwen3_vl": "Qwen3-VL enrichment",
}
QWEN_MODEL_DISPLAY_NAMES = {
    "qwen3-vl-8b-instruct-4bit": "Qwen3-VL 8B",
    "qwen3-vl-4b-instruct-4bit": "Qwen3-VL 4B",
}

PackageProbe = Callable[[str], bool]


@dataclass(frozen=True)
class CapabilityEnvironment:
    """Small injectable runtime inventory; provider discovery never imports ONNX Runtime."""

    system: str
    machine: str
    package_available: PackageProbe
    providers: frozenset[str]
    torch_devices: frozenset[str] = frozenset()
    ctranslate2_devices: frozenset[str] = frozenset()

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


@lru_cache(maxsize=None)
def _installed_package(name: str) -> bool:
    """Return package presence without importing it or executing package code."""

    try:
        return find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


@lru_cache(maxsize=1)
def _packaged_onnx_providers() -> frozenset[str]:
    """Return only the provider proven by the base packaged ORT distribution.

    Accelerator library filenames and Python package stubs do not prove that a provider
    can initialize on the current machine.  Importing ORT merely to ask it would load a
    native inference runtime in the capability endpoint.  Until a frozen build supplies
    a verified provider inventory, the side-effect-free source runtime therefore makes
    the conservative CPU claim only.
    """

    if not _installed_package("onnxruntime"):
        return frozenset()
    return frozenset({"CPUExecutionProvider"})


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

    # Tests and frozen-runtime packaging can inject an exact, already-verified
    # accelerator inventory through CapabilityEnvironment. Package/module presence by
    # itself is deliberately insufficient to advertise Core ML, MPS, or CUDA.
    system = platform.system()
    machine = platform.machine()
    target = CapabilityEnvironment(
        system=system,
        machine=machine,
        package_available=_installed_package,
        providers=_packaged_onnx_providers(),
        torch_devices=frozenset(),
    ).target_id
    return CapabilityEnvironment(
        system=system,
        machine=machine,
        package_available=_installed_package,
        providers=_packaged_onnx_providers(),
        torch_devices=frozenset(),
        ctranslate2_devices=(
            _verified_ctranslate2_devices()
            if target in {"windows-x64", "linux-x86_64"}
            else frozenset()
        ),
    )


class CapabilityUnavailableError(ValueError):
    """A selected or required backend cannot run in this packaged runtime."""


def _download(
    backend_id: str, weight_requirement: Literal["none", "on_demand"]
) -> ModelDownloadState:
    if weight_requirement == "none":
        return ModelDownloadState(state="not_required")
    # This read verifies only the manifest-controlled cache. It never imports a model,
    # creates a directory, contacts the network, or starts acquisition.
    from scanner.model_acquisition import get_acquisition_service

    return get_acquisition_service().backend_status(backend_id)


def _capability(
    backend_id: str,
    stage: Literal["audio", "detector", "enrichment"],
    *,
    available: bool,
    reason: AvailabilityReason | None = None,
    device: Literal["cpu", "cuda", "coreml", "metal", "mlx", "none"] = "none",
    weight_requirement: Literal["none", "on_demand"] = "on_demand",
    enrichment_behavior: Literal["none", "qwen_summary", "detector_findings_only"] = "none",
) -> ModelCapability:
    """Build one backend capability record from availability and device information."""
    return ModelCapability(
        id=backend_id,
        display_name=DISPLAY_NAMES[backend_id],
        stage=stage,
        availability="available" if available else "unavailable",
        reason=None if available else reason,
        device=device if available else "none",
        weight_requirement=weight_requirement,
        download=_download(backend_id, weight_requirement),
        enrichment_behavior=enrichment_behavior,
    )


def _missing_package(environment: CapabilityEnvironment, *packages: str) -> bool:
    return not all(environment.package_available(package) for package in packages)


def _target_failure(
    environment: CapabilityEnvironment,
) -> AvailabilityReason | None:
    if environment.target_id != "unsupported":
        return None
    if environment.system in {"Darwin", "Windows", "Linux"}:
        return "unsupported_architecture"
    return "unsupported_platform"


def model_capabilities(
    environment: CapabilityEnvironment | None = None,
) -> tuple[ModelCapability, ...]:
    """Return the complete, ordered capability matrix for one injected environment."""

    environment = environment or current_environment()
    target_failure = _target_failure(environment)

    def standard(
        backend_id: str,
        stage: Literal["audio", "detector"],
        packages: tuple[str, ...],
        *,
        device: Literal["cpu", "cuda", "coreml", "metal", "mlx"] = "cpu",
        provider: str | None = None,
        weight_requirement: Literal["none", "on_demand"] = "on_demand",
    ) -> ModelCapability:
        """Create a standard model capability using the supplied backend metadata."""
        if target_failure:
            return _capability(
                backend_id,
                stage,
                available=False,
                reason=target_failure,
                weight_requirement=weight_requirement,
            )
        if _missing_package(environment, *packages):
            return _capability(
                backend_id,
                stage,
                available=False,
                reason="missing_package",
                weight_requirement=weight_requirement,
            )
        if provider is not None and provider not in environment.providers:
            return _capability(
                backend_id,
                stage,
                available=False,
                reason="missing_provider",
                weight_requirement=weight_requirement,
            )
        return _capability(
            backend_id,
            stage,
            available=True,
            device=device,
            weight_requirement=weight_requirement,
        )

    capabilities: list[ModelCapability] = [
        standard(
            "whisper",
            "audio",
            ("faster_whisper",),
            device=(
                "cuda"
                if environment.target_id in {"windows-x64", "linux-x86_64"}
                and "cuda" in environment.ctranslate2_devices
                else "cpu"
            ),
        ),
        standard(
            "mlx_whisper",
            "audio",
            ("mlx_whisper", "mlx"),
            device="mlx",
        )
        if environment.target_id == "macos-arm64"
        else _capability(
            "mlx_whisper",
            "audio",
            available=False,
            reason=target_failure or "unsupported_platform",
        ),
        standard("better_profanity", "audio", ("better_profanity",), weight_requirement="none"),
        standard(
            "glin_profanity",
            "audio",
            ("glin_profanity",),
            weight_requirement="none",
        ),
        standard(
            "alt_profanity_check",
            "audio",
            ("profanity_check",),
            weight_requirement="none",
        ),
        standard(
            "nudenet",
            "detector",
            ("nudenet", "onnxruntime"),
            device=(
                "coreml"
                if environment.target_id == "macos-arm64"
                and "CoreMLExecutionProvider" in environment.providers
                else "cpu"
            ),
            provider="CPUExecutionProvider",
            weight_requirement="none",
        ),
        standard(
            "image_safety_gate",
            "detector",
            (
                "numpy",
                "pandas",
                "onnxruntime",
                "torch",
                "transformers",
                "huggingface_hub",
                "PIL",
                "timm",
                "safetensors",
            ),
            device=(
                "metal"
                if environment.target_id == "macos-arm64"
                and "mps" in environment.torch_devices
                else "cpu"
            ),
            provider="CPUExecutionProvider",
        ),
        standard(
            "siglip2_moderation_nsfw",
            "detector",
            ("numpy", "onnxruntime", "torch", "transformers", "huggingface_hub", "PIL"),
            device=(
                "metal"
                if environment.target_id == "macos-arm64"
                and "mps" in environment.torch_devices
                else "cpu"
            ),
            provider="CPUExecutionProvider",
        ),
        standard(
            "siglip2_moderation_violence",
            "detector",
            ("numpy", "onnxruntime", "torch", "transformers", "huggingface_hub", "PIL"),
            device=(
                "metal"
                if environment.target_id == "macos-arm64"
                and "mps" in environment.torch_devices
                else "cpu"
            ),
            provider="CPUExecutionProvider",
        ),
    ]

    if target_failure:
        qwen = _capability("qwen3_vl", "enrichment", available=False, reason=target_failure)
    elif environment.target_id in {"windows-x64", "linux-x86_64"}:
        qwen = _capability(
            "qwen3_vl",
            "enrichment",
            available=False,
            reason="deferred_gap",
            enrichment_behavior="detector_findings_only",
        )
    elif _missing_package(environment, "huggingface_hub", "mlx_vlm", "mlx"):
        qwen = _capability("qwen3_vl", "enrichment", available=False, reason="missing_package")
    else:
        qwen = _capability(
            "qwen3_vl",
            "enrichment",
            available=True,
            device="mlx",
            enrichment_behavior="qwen_summary",
        )
    capabilities.extend(
        [
            qwen.model_copy(
                update={
                    "id": "qwen",
                    "display_name": DISPLAY_NAMES["qwen"],
                    "stage": "audio",
                    "enrichment_behavior": "none",
                }
            ),
            qwen,
        ]
    )
    return tuple(capabilities)


def capability_for(
    backend_id: str,
    *,
    environment: CapabilityEnvironment | None = None,
) -> ModelCapability:
    backend_id = {"alt-profanity-check": "alt_profanity_check"}.get(backend_id, backend_id)
    for capability in model_capabilities(environment):
        if capability.id == backend_id:
            return capability
    raise ValueError(f"Unsupported model backend: {backend_id}")


def require_available(
    backend_id: str,
    *,
    environment: CapabilityEnvironment | None = None,
) -> ModelCapability:
    capability = capability_for(backend_id, environment=environment)
    if capability.availability == "unavailable":
        detail = capability.reason.replace("_", " ") if capability.reason else "unavailable"
        raise CapabilityUnavailableError(f"{capability.display_name} is unavailable: {detail}")
    return capability


def qwen_enrichment_capability(
    environment: CapabilityEnvironment | None = None,
) -> ModelCapability:
    return capability_for("qwen3_vl", environment=environment)
