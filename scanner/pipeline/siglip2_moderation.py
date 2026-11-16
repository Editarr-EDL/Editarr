"""SigLIP2 embeddings with local ONNX moderation heads for video frames."""

from __future__ import annotations

import importlib
from pathlib import Path
from threading import Lock

from scanner.model_acquisition import get_acquisition_service
from scanner.settings.capabilities import capability_for
from scanner.settings.config import settings

_ENCODER_REPO_ID = "google/siglip2-base-patch16-256"
_HEAD_REPO_ID = "khasinski/siglip2-moderation-heads"
_HEAD_NAMES = {"nsfw", "violence"}
_CACHE: dict[str, object] = {}
_LOAD_LOCK = Lock()


def _model_root() -> Path:
    """Return the regenerable SigLIP2 root after cache env setup."""

    return settings.model_cache / "siglip2_moderation"


def _require_dependency(import_name: str, install_hint: str):
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "SigLIP2 Moderation Heads require optional dependency "
            f"'{install_hint}'. Install the stack dependencies first."
        ) from exc


def _select_torch_device(torch):
    capability = capability_for("siglip2_moderation_nsfw")
    if capability.device == "metal" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _load_encoder():
    """Acquire and cache the installed SigLIP2 encoder on the selected device."""
    cache_key = "encoder"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    with _LOAD_LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        torch = _require_dependency("torch", "torch")
        transformers = _require_dependency("transformers", "transformers")
        model_dir = get_acquisition_service().acquire("siglip2-base-patch16-256")
        processor = transformers.AutoProcessor.from_pretrained(str(model_dir))
        model = transformers.AutoModel.from_pretrained(str(model_dir))
        device = _select_torch_device(torch)
        model.to(device)
        model.eval()
        _CACHE[cache_key] = (processor, model, device, torch)
        return _CACHE[cache_key]


def _load_head(head_name: str):
    """Load the moderation classification head for the requested category."""
    if head_name not in _HEAD_NAMES:
        raise ValueError(f"Unsupported SigLIP2 moderation head: {head_name}")
    cache_key = f"head_{head_name}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    with _LOAD_LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        onnxruntime = _require_dependency("onnxruntime", "onnxruntime")
        backend_id = f"siglip2_moderation_{head_name}"
        model_dir = get_acquisition_service().acquire(
            f"siglip2-moderation-head-{head_name}"
        )
        filenames = (f"{head_name}_head.onnx", f"{head_name}_head.onnx.data")
        capability = capability_for(backend_id)
        provider_candidates = (
            ("CoreMLExecutionProvider", "CPUExecutionProvider")
            if capability.device == "metal"
            else ("CPUExecutionProvider",)
        )
        providers = [
            provider
            for provider in provider_candidates
            if provider in onnxruntime.get_available_providers()
        ]
        _CACHE[cache_key] = onnxruntime.InferenceSession(
            str(model_dir / filenames[0]),
            providers=providers or ["CPUExecutionProvider"],
        )
        return _CACHE[cache_key]


def _image_embedding(features):
    """Unwrap the pooled image embedding across supported Transformers versions."""
    return getattr(features, "pooler_output", features)


def score_images(frame_paths: list[Path], head_name: str) -> list[float]:
    """Return moderation probabilities for an image batch using one moderation head."""
    if not frame_paths:
        return []
    image_module = _require_dependency("PIL.Image", "pillow")
    numpy = _require_dependency("numpy", "numpy")
    processor, encoder, device, torch = _load_encoder()
    head = _load_head(head_name)

    images = []
    for frame_path in frame_paths:
        with image_module.open(frame_path) as image:
            images.append(image.convert("RGB"))
    inputs = processor(images=images, return_tensors="pt")
    device_inputs = {
        key: value.to(device) if torch.is_tensor(value) else value
        for key, value in inputs.items()
    }
    with torch.inference_mode():
        embedding = _image_embedding(encoder.get_image_features(**device_inputs))
        embedding = torch.nn.functional.normalize(embedding, dim=-1)
    output = head.run(None, {"embedding": embedding.detach().cpu().float().numpy()})[0]
    logits = numpy.asarray(output).reshape(-1)
    if len(logits) != len(frame_paths):
        raise RuntimeError("SigLIP2 moderation head returned an unexpected batch size")
    return [float(1.0 / (1.0 + numpy.exp(-logit))) for logit in logits]


def score_image(frame_path: Path, head_name: str) -> float:
    """Return a moderation probability for one image using the requested head."""
    return score_images([frame_path], head_name)[0]


def threshold_for_head(head_name: str) -> float:
    if head_name == "nsfw":
        return settings.siglip2_moderation_nsfw_threshold
    if head_name == "violence":
        return settings.siglip2_moderation_violence_threshold
    raise ValueError(f"Unsupported SigLIP2 moderation head: {head_name}")
