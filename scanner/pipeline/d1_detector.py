"""LiquidAI d1-3B as a video detector with its own severity questions.

d1-3B is a decision model: one forward pass answers several typed questions about a
frame without generating text. Each frame gets one ``noul`` (P(yes)) question per family
and one ``score`` severity question per family, using the shared rubric levels. The
model runs through transformers with ``trust_remote_code`` from the pinned,
hash-verified artifact directory only (MPS where verified on macOS, else CPU), following
the model card's sample code.
"""

from __future__ import annotations

import gc
import logging
import math
import sys
from pathlib import Path
from threading import Lock

from scanner.evaluation.severity_rubric import RUBRIC
from scanner.model_acquisition import acquire_artifact
from scanner.models import McfCategoryFamily
from scanner.settings.capabilities import (
    D1_MIN_TRANSFORMERS,
    current_environment,
    d1_transformers_supported,
    require_available,
)

from .decision_detection import (
    DETECTED_FAMILIES,
    DecisionDetector,
    FrameDecision,
    family_question,
)
from .dependencies import require_dependency

ARTIFACT_ID = "d1-3b"
MODEL_ID = "LiquidAI/d1-3B"
MODEL_REVISION = "05b80faf55536582ad5ffb2da7456f6a653acf71"
# The third-party imports of this revision's remote code, from remote_code_imports(); the
# frozen runtime smoke loads them through transformers' remote-code loader. Update both
# when MODEL_REVISION changes.
REMOTE_CODE_IMPORTS = {
    "PIL": ("Image",),
    "torch": (),
    "torch.nn": (),
    "torch.nn.functional": (),
    "transformers": (
        "AutoModelForImageTextToText",
        "AutoProcessor",
        "AutoTokenizer",
        "Lfm2Config",
        "Lfm2VlConfig",
        "PretrainedConfig",
    ),
    "transformers.modeling_layers": ("GradientCheckpointingLayer",),
    "transformers.modeling_outputs": ("BaseModelOutputWithPast",),
    "transformers.models.lfm2.modeling_lfm2": ("Lfm2ForCausalLM",),
    "transformers.models.lfm2_vl.modeling_lfm2_vl": ("Lfm2VlForConditionalGeneration",),
}
# Modules transformers imports by name while loading this artifact's processor and model.
RESOLVED_MODULES = (
    "transformers.models.lfm2_vl.image_processing_lfm2_vl",
    "transformers.models.lfm2_vl.processing_lfm2_vl",
    "transformers.models.siglip2.modeling_siglip2",
    "transformers.tokenization_utils_tokenizers",
)
# Violence uses 0.25: with only the four family questions, it reproduces the decisions of
# the original question set (family plus specific tokens such as weapons, each at 0.5) on
# 1592 of 1603 evaluation frames and raises violence F1 from 0.57 to 0.65. The other
# families scored best at 0.5.
THRESHOLDS = {
    McfCategoryFamily.DRUGS: 0.5,
    McfCategoryFamily.NUDITY: 0.5,
    McfCategoryFamily.SEX: 0.5,
    McfCategoryFamily.VIOLENCE: 0.25,
}
SEVERITY_SUBJECTS = {
    McfCategoryFamily.VIOLENCE: "violence",
    McfCategoryFamily.SEX: "sexual content",
    McfCategoryFamily.NUDITY: "nudity",
    McfCategoryFamily.DRUGS: "drug, alcohol or tobacco use",
}
_CACHE: dict[str, object] = {}
_LOCK = Lock()
logger = logging.getLogger(__name__)
# Unusable-answer kinds already logged by this worker process (one scan per process).
_WARNED: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message + " (logged once)", *args)


def _require_dependency(import_name: str, install_hint: str):
    return require_dependency(import_name, install_hint, "d1-3B requires")


def questions() -> dict[str, dict]:
    """Family ``noul`` questions, then rubric ``score`` questions, in evaluated order."""
    asked: dict[str, dict] = {
        family.value: {"type": "noul", "instructions": family_question(family)}
        for family in DETECTED_FAMILIES
    }
    for family, levels in RUBRIC.items():
        asked[f"sev_{family.value}"] = {
            "type": "score",
            "instructions": f"How severe is the {SEVERITY_SUBJECTS[family]} in this video frame?",
            "criteria": [text[0].upper() + text[1:] for text in levels],
        }
    return asked


def severity_level(answer: object, levels: int) -> int | None:
    """Argmax level of a ``score`` answer, or None when its distribution is unusable.

    The ``probabilities`` mapping must have exactly the keys ``"0"`` to ``levels - 1``,
    each a finite, non-negative number, with a positive sum. Anything else yields no
    detector severity, so the description model or confidence mapping decides instead.
    """
    probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
    if not isinstance(probabilities, dict) or set(probabilities) != {
        str(level) for level in range(levels)
    }:
        return None
    values = [probabilities[str(level)] for level in range(levels)]
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        for value in values
    ) or sum(values) <= 0:
        return None
    return max(range(levels), key=values.__getitem__)


def parse_answers(response: dict) -> FrameDecision:
    """Map a ``system_one`` response to scores and argmax severity levels.

    A malformed answer never fails the scan. Like the other detectors, whose
    non-finite scores never reach a threshold, a missing, non-numeric, or non-finite
    family decision leaves that family undecided (not flagged) for the frame. A family
    whose severity answer is unusable gets no detector severity level. Each kind of
    problem is logged once per worker process.
    """
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        _warn_once("answers", "d1-3B returned no answers for a frame; skipping its decisions")
        answers = {}
    scores: dict[McfCategoryFamily, float] = {}
    levels: dict[McfCategoryFamily, int] = {}
    for family in DETECTED_FAMILIES:
        answer = answers.get(family.value)
        value = answer.get("noul") if isinstance(answer, dict) else None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            if answers:
                _warn_once(
                    f"decision:{family.value}",
                    "d1-3B returned an unusable %s decision; leaving it undecided",
                    family.value,
                )
            continue
        scores[family] = min(1.0, max(0.0, float(value)))
    for family, ladder in RUBRIC.items():
        if family not in scores:
            continue
        level = severity_level(answers.get(f"sev_{family.value}"), len(ladder))
        if level is not None:
            levels[family] = level
        else:
            _warn_once(
                f"severity:{family.value}",
                "d1-3B returned an unusable %s severity distribution; using the "
                "description or confidence severity instead",
                family.value,
            )
    return FrameDecision(scores=scores, thresholds=THRESHOLDS, levels=levels)


def _require_transformers_version() -> None:
    if not d1_transformers_supported():
        minimum = ".".join(map(str, D1_MIN_TRANSFORMERS))
        raise RuntimeError(f"d1-3B requires transformers>={minimum}")


def _device(torch) -> str:
    """MPS where the capability verified Metal, otherwise CPU.

    Like the other torch detectors this never touches ``torch.cuda``: the Windows and
    Linux runtimes ship CPU-only torch and exclude its CUDA stack.
    """
    if current_environment().torch_device == "metal" and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_model(model_dir: Path | None = None):
    """Load the model once per process from local files only.

    Acquires the pinned artifact unless ``model_dir`` names an already verified local
    copy, in which case nothing is acquired or downloaded.
    """
    require_available("d1-3b")
    if "model" in _CACHE:
        return _CACHE["model"]
    torch = _require_dependency("torch", "torch")
    transformers = _require_dependency("transformers", "transformers")
    _require_transformers_version()
    if model_dir is None:
        model_dir = acquire_artifact(ARTIFACT_ID)
    device = _device(torch)
    model = transformers.AutoModel.from_pretrained(
        str(model_dir),
        trust_remote_code=True,
        local_files_only=True,
        # bf16 is unsupported or slow on many CPUs; fp32 is the model card's CPU dtype.
        dtype=torch.float32 if device == "cpu" else torch.bfloat16,
    ).to(device)
    _CACHE["model"] = model.eval()
    return _CACHE["model"]


def release() -> bool:
    """Drop the loaded model and return its accelerator memory; False if none was loaded."""
    with _LOCK:
        model = _CACHE.pop("model", None)
    if model is None:
        return False
    del model
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        torch.mps.empty_cache()
    return True


def score_frame(frame_path: Path, *, model_dir: Path | None = None) -> FrameDecision:
    """Ask every family and severity question about one frame in one pass.

    ``model_dir`` loads from a verified local artifact instead of acquiring one.
    """
    image_module = _require_dependency("PIL.Image", "pillow")
    with image_module.open(frame_path) as opened:
        image = opened.convert("RGB")
    with _LOCK:
        model = _load_model(model_dir)
        response = model.system_one(None, questions(), images=[image])
    return parse_answers(response)


DETECTOR = DecisionDetector(
    video_model="d1-3b",
    label_prefix="d1",
    model_id=MODEL_ID,
    model_revision=MODEL_REVISION,
    score=score_frame,
)
