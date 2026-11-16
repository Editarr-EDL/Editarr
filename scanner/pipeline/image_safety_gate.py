"""Optional Image Safety Gate model stack for frame-level visual detection."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from scanner.model_acquisition import get_acquisition_service, manifest
from scanner.settings.capabilities import capability_for
from scanner.settings.config import settings

_WD_VARIANT_REPOS = {
    "vit": "SmilingWolf/wd-vit-tagger-v3",
    "convnext": "SmilingWolf/wd-convnext-tagger-v3",
    "swinv2": "SmilingWolf/wd-swinv2-tagger-v3",
    "vit-large": "SmilingWolf/wd-vit-large-tagger-v3",
    "eva02-large": "SmilingWolf/wd-eva02-large-tagger-v3",
}
_WD_FILES = ("model.onnx", "selected_tags.csv")
_WD_UNSAFE_RATING_NAMES = ("questionable", "explicit")
_WD_RATING_CATEGORY = 9

_CLIP_REPO_ID = "CompVis/stable-diffusion-safety-checker"
_CLIP_FILES = ("config.json", "preprocessor_config.json", "pytorch_model.bin")

_NSFL_REPO_ID = "OwenElliott/image-safety-classifier-s"
_NSFL_FILES = ("config.json", "model.safetensors")
_VISION_STATE_PREFIXES = ("vision_model.vision_model.", "vision_model.")

_CACHE: dict[str, object] = {}
_LOAD_LOCK = Lock()


def _model_root() -> Path:
    """Return the regenerable Image Safety Gate root after cache env setup."""

    return settings.model_cache / "image_safety_gate"


def _coreml_cache_dir() -> Path:
    return _model_root() / "coreml-cache"


@dataclass
class ImageSafetyGateResult:
    wd_questionable: float
    wd_explicit: float
    wd_total: float
    clip_max_score: float
    clip_confidence: float
    nsfl_score: float
    wd_unsafe: bool
    clip_unsafe: bool
    nsfl_unsafe: bool


def _require_dependency(import_name: str, install_hint: str):
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Image Safety Gate model requires optional dependency "
            f"'{install_hint}'. Install the stack dependencies first."
        ) from exc


def _ensure_files(repo_id: str, model_dir: Path, names: tuple[str, ...]) -> Path:
    artifact = next(
        (item for item in manifest().artifacts if item.repository == repo_id),
        None,
    )
    if artifact is None or tuple(item.path for item in artifact.files) != names:
        raise RuntimeError(f"No exact managed-artifact declaration exists for {repo_id}")
    # ``model_dir`` remains in the signature for focused loader compatibility tests;
    # the acquisition service is the sole authority for the actual cache location.
    del model_dir
    return get_acquisition_service().acquire(artifact.id)


def _create_wd_onnx_session(onnxruntime, model_path: Path):
    """Create a WD session using the best available execution provider.

    WD v3 exports use a symbolic ``batch_size`` dimension.  Resolving it for
    the frame-at-a-time gate lets Core ML compile a static ML Program instead
    of repeatedly dispatching a fragmented dynamic-shape NeuralNetwork graph.
    The compiled Core ML artifacts are cached because visual scans intentionally
    run in disposable worker processes.
    """
    available_providers = set(onnxruntime.get_available_providers())
    capability = capability_for("image_safety_gate")
    if capability.device == "cpu":
        return onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
    if "CoreMLExecutionProvider" in available_providers:
        coreml_cache_dir = _coreml_cache_dir()
        coreml_cache_dir.mkdir(parents=True, exist_ok=True)
        session_options = onnxruntime.SessionOptions()
        session_options.add_free_dimension_override_by_name("batch_size", 1)
        return onnxruntime.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=[
                (
                    "CoreMLExecutionProvider",
                    {
                        "ModelFormat": "MLProgram",
                        # The EVA02 MLProgram's GPU allocations grow on every
                        # frame when it shares Metal with the gate's PyTorch
                        # models.  Neural Engine execution remains bounded and
                        # leaves MPS memory available for CLIP and NSFL.
                        "MLComputeUnits": "CPUAndNeuralEngine",
                        "RequireStaticInputShapes": "1",
                        "EnableOnSubgraphs": "0",
                        "SpecializationStrategy": "FastPrediction",
                        "ModelCacheDirectory": str(coreml_cache_dir.resolve()),
                    },
                ),
                "CPUExecutionProvider",
            ],
        )

    return onnxruntime.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
    )


def _load_wd_tagger(variant: str):
    """Load and cache the local WD tagger and its labels for safety classification."""
    if variant not in _WD_VARIANT_REPOS:
        raise ValueError(f"Unsupported Image Safety Gate WD variant: {variant}")
    cache_key = f"wd_{variant}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    with _LOAD_LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        numpy = _require_dependency("numpy", "numpy")
        pandas = _require_dependency("pandas", "pandas")
        onnxruntime = _require_dependency("onnxruntime", "onnxruntime")

        model_dir = _ensure_files(
            _WD_VARIANT_REPOS[variant],
            _model_root() / f"wd-{variant}-tagger-v3",
            _WD_FILES,
        )
        tags_df = pandas.read_csv(model_dir / "selected_tags.csv")
        rating_rows = tags_df[tags_df["category"] == _WD_RATING_CATEGORY]
        rating_indices = {row["name"]: int(idx) for idx, row in rating_rows.iterrows()}
        for name in _WD_UNSAFE_RATING_NAMES:
            if name not in rating_indices:
                raise RuntimeError(
                    f"WD tagger ratings CSV is missing '{name}' index"
                )
        unsafe_indices = tuple(rating_indices[name] for name in _WD_UNSAFE_RATING_NAMES)
        session = _create_wd_onnx_session(onnxruntime, model_dir / "model.onnx")
        input_meta = session.get_inputs()[0]
        _, height, _, _ = input_meta.shape
        target_size = int(height) if isinstance(height, int) else 448
        _CACHE[cache_key] = (session, input_meta.name, target_size, unsafe_indices, numpy)
        return _CACHE[cache_key]


def _preprocess_for_wd(pil_image, target_size: int, numpy):
    """Resize and normalize an image into the WD tagger input tensor."""
    image_module = _require_dependency("PIL.Image", "pillow")
    image = pil_image.convert("RGBA")
    background = image_module.new("RGBA", image.size, (255, 255, 255))
    background.paste(image, mask=image.split()[3])
    image = background.convert("RGB")

    width, height = image.size
    side = max(width, height)
    square = image_module.new("RGB", (side, side), (255, 255, 255))
    square.paste(image, ((side - width) // 2, (side - height) // 2))

    if side != target_size:
        square = square.resize((target_size, target_size), image_module.BICUBIC)

    array = numpy.asarray(square, dtype=numpy.float32)
    return array[..., ::-1].copy()


def _run_wd_tagger(pil_image, variant: str) -> tuple[float, float]:
    session, input_name, target_size, unsafe_indices, numpy = _load_wd_tagger(variant)
    batch = numpy.stack([_preprocess_for_wd(pil_image, target_size, numpy)], axis=0)
    output = session.run(None, {input_name: batch})[0]
    questionable = float(output[0, unsafe_indices[0]])
    explicit = float(output[0, unsafe_indices[1]])
    return questionable, explicit


def _select_torch_device(torch):
    capability = capability_for("image_safety_gate")
    if capability.device == "metal" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _vision_state_dict(state_dict: dict[str, object]) -> dict[str, object]:
    """Extract vision weights from either safety-checker checkpoint layout."""
    vision_state: dict[str, object] = {}
    for key, value in state_dict.items():
        prefix = next((item for item in _VISION_STATE_PREFIXES if key.startswith(item)), None)
        if prefix:
            vision_state[key[len(prefix) :]] = value
    if not vision_state:
        raise RuntimeError("CLIP safety checkpoint did not contain vision model weights")
    # Older safety-checker checkpoints persist this derived buffer, while
    # current Transformers recreates it when constructing CLIPVisionModel.
    vision_state.pop("embeddings.position_ids", None)
    return vision_state


def _load_clip_model():
    """Load the installed CLIP safety model and its processor on the selected device."""
    cache_key = "clip"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    with _LOAD_LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        torch = _require_dependency("torch", "torch")
        transformers = _require_dependency("transformers", "transformers")
        device = _select_torch_device(torch)

        model_dir = _ensure_files(
            _CLIP_REPO_ID,
            _model_root() / "stable-diffusion-safety-checker",
            _CLIP_FILES,
        )
        processor = transformers.CLIPImageProcessor.from_pretrained(str(model_dir))
        # Share one checkpoint state dict between concept embeddings and vision weights.
        state_dict = torch.load(model_dir / "pytorch_model.bin", map_location="cpu")
        config = transformers.CLIPVisionConfig.from_pretrained(str(model_dir))
        vision_model = transformers.CLIPVisionModel(config)
        vision_state = _vision_state_dict(state_dict)
        incompatible = vision_model.load_state_dict(vision_state, strict=True)
        if incompatible.missing_keys or incompatible.unexpected_keys:
            raise RuntimeError("CLIP safety checkpoint did not match its vision model")

        concept_embeds = state_dict["concept_embeds"].to(device)
        concept_weights = state_dict["concept_embeds_weights"].to(device)
        projection_weight = state_dict["visual_projection.weight"].to(device)
        # Release the full CPU checkpoint before allocating the MPS copy of
        # the vision model.  PyTorch's allocator can otherwise preserve an
        # additional gigabyte for the rest of the scan.
        del vision_state
        del state_dict

        vision_model.to(device)
        vision_model.eval()
        _CACHE[cache_key] = (
            vision_model,
            projection_weight,
            processor,
            concept_embeds,
            concept_weights,
            device,
            torch,
        )
        return _CACHE[cache_key]


def _run_clip_safety(pil_image, sensitivity: float) -> tuple[float, bool, float]:
    """Score an image with CLIP and return the normalized safety result."""
    (
        vision_model,
        projection_weight,
        processor,
        concept_embeds,
        concept_weights,
        device,
        torch,
    ) = _load_clip_model()
    clip_inputs = processor([pil_image], return_tensors="pt")
    pixel_values = clip_inputs.pixel_values.to(device)
    with torch.no_grad():
        pooled = vision_model(pixel_values).pooler_output
        image_embeds = torch.nn.functional.linear(pooled, projection_weight)
        image_embeds = torch.nn.functional.normalize(image_embeds, dim=-1)
        target = torch.nn.functional.normalize(concept_embeds, dim=-1)
        similarity = torch.nn.functional.cosine_similarity(
            image_embeds.unsqueeze(1),
            target.unsqueeze(0),
            dim=-1,
        )
        adjusted = -0.1 + (0.14 * sensitivity)
        concept_scores = similarity - concept_weights.unsqueeze(0) + adjusted
        clip_max = float(concept_scores.max().item())
    unsafe = clip_max > 0.0
    confidence = max(0.0, min(1.0, 0.5 + clip_max))
    return clip_max, unsafe, confidence


def _normalize_logits(output, torch):
    if isinstance(output, (tuple, list)):
        tensors = [item for item in output if torch.is_tensor(item)]
        if not tensors:
            raise RuntimeError("NSFL model output did not contain a tensor")
        return tensors[0]
    return output


def _load_nsfl_model():
    """Load and cache the installed NSFL classifier for image safety checks."""
    cache_key = "nsfl"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    with _LOAD_LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        timm = _require_dependency("timm", "timm")
        torch = _require_dependency("torch", "torch")
        safetensors_torch = _require_dependency("safetensors.torch", "safetensors")

        model_dir = _ensure_files(
            _NSFL_REPO_ID,
            _model_root() / "image-safety-classifier-s",
            _NSFL_FILES,
        )
        with (model_dir / "config.json").open("r", encoding="utf-8") as file:
            config = json.load(file)

        architecture = str(config["architecture"])
        pretrained_cfg = dict(config.get("pretrained_cfg") or {})
        num_classes = int(config.get("num_classes") or 3)
        class_names = tuple(pretrained_cfg.get("label_names") or ("NSFL", "NSFW", "SFW"))

        transform = timm.data.create_transform(
            input_size=tuple(pretrained_cfg.get("input_size") or (3, 224, 224)),
            is_training=False,
            interpolation=str(pretrained_cfg.get("interpolation") or "bicubic"),
            mean=tuple(pretrained_cfg.get("mean") or (0.485, 0.456, 0.406)),
            std=tuple(pretrained_cfg.get("std") or (0.229, 0.224, 0.225)),
            crop_pct=float(pretrained_cfg.get("crop_pct") or 0.95),
            crop_mode=str(pretrained_cfg.get("crop_mode") or "center"),
        )

        model = timm.create_model(architecture, pretrained=False, num_classes=num_classes)
        model.load_state_dict(
            safetensors_torch.load_file(str(model_dir / "model.safetensors"), device="cpu")
        )
        device = _select_torch_device(torch)
        model.to(device)
        model.eval()

        nsfl_idx = class_names.index("NSFL") if "NSFL" in class_names else 0
        _CACHE[cache_key] = (model, transform, nsfl_idx, device, torch)
        return _CACHE[cache_key]


def _run_nsfl(pil_image) -> float:
    model, transform, nsfl_idx, device, torch = _load_nsfl_model()
    with torch.inference_mode():
        inputs = transform(pil_image.convert("RGB")).unsqueeze(0).to(device)
        logits = _normalize_logits(model(inputs), torch)
        probs = torch.softmax(logits, dim=-1).detach().cpu()
    return float(probs[0, nsfl_idx].item())


def scan_image_safety_gate(frame_path: Path) -> ImageSafetyGateResult:
    """Run every Image Safety Gate stage on one source frame."""
    image_module = _require_dependency("PIL.Image", "pillow")

    wd_variant = settings.image_safety_gate_wd_variant
    sensitivity = settings.image_safety_gate_sensitivity
    nsfw_threshold = settings.image_safety_gate_nsfw_threshold
    nsfl_threshold = settings.image_safety_gate_nsfl_threshold

    with image_module.open(frame_path) as pil_image:
        wd_questionable, wd_explicit = _run_wd_tagger(pil_image, wd_variant)
        clip_max, clip_unsafe, clip_confidence = _run_clip_safety(pil_image, sensitivity)
        nsfl_score = _run_nsfl(pil_image)

    wd_total = wd_questionable + wd_explicit
    wd_unsafe = wd_total >= nsfw_threshold
    nsfl_unsafe = nsfl_score >= nsfl_threshold

    return ImageSafetyGateResult(
        wd_questionable=wd_questionable,
        wd_explicit=wd_explicit,
        wd_total=wd_total,
        clip_max_score=clip_max,
        clip_confidence=clip_confidence,
        nsfl_score=nsfl_score,
        wd_unsafe=wd_unsafe,
        clip_unsafe=clip_unsafe,
        nsfl_unsafe=nsfl_unsafe,
    )
