"""LiquidAI d1-3B as a video detector with its own severity questions.

d1-3B is a decision model: one forward pass answers several typed questions about a
frame without generating text. Each frame gets one ``noul`` (P(yes)) question per family
and one ``score`` severity question per family, using the shared rubric levels. The
model runs through transformers with ``trust_remote_code`` from the pinned,
hash-verified artifact directory only (MPS on macOS, CUDA or CPU elsewhere), following
the model card's sample code.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path
from threading import Lock

from scanner.evaluation.severity_rubric import RUBRIC
from scanner.model_acquisition import acquire_artifact
from scanner.models import McfCategoryFamily
from scanner.settings.capabilities import (
    D1_MIN_TRANSFORMERS,
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


def parse_answers(response: dict) -> FrameDecision:
    """Map a ``system_one`` response to scores and argmax severity levels.

    Raises ValueError when an answer is missing or malformed.
    """
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("d1-3B returned no answers")
    scores: dict[McfCategoryFamily, float] = {}
    levels: dict[McfCategoryFamily, int] = {}
    for family in DETECTED_FAMILIES:
        answer = answers.get(family.value)
        value = answer.get("noul") if isinstance(answer, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"d1-3B returned no decision for {family.value}")
        scores[family] = min(1.0, max(0.0, float(value)))
    for family, ladder in RUBRIC.items():
        answer = answers.get(f"sev_{family.value}")
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if not isinstance(probabilities, dict):
            raise ValueError(f"d1-3B returned no severity for {family.value}")
        values = [float(probabilities.get(str(level), 0.0)) for level in range(len(ladder))]
        levels[family] = max(range(len(values)), key=values.__getitem__)
    return FrameDecision(scores=scores, thresholds=THRESHOLDS, levels=levels)


def _require_transformers_version() -> None:
    if not d1_transformers_supported():
        minimum = ".".join(map(str, D1_MIN_TRANSFORMERS))
        raise RuntimeError(f"d1-3B requires transformers>={minimum}")


def _device(torch) -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_model():
    """Acquire the pinned artifact and load it once per process from local files only."""
    require_available("d1-3b")
    if "model" in _CACHE:
        return _CACHE["model"]
    torch = _require_dependency("torch", "torch")
    transformers = _require_dependency("transformers", "transformers")
    _require_transformers_version()
    model_dir = acquire_artifact(ARTIFACT_ID)
    device = _device(torch)
    model = transformers.AutoModel.from_pretrained(
        str(model_dir),
        trust_remote_code=True,
        local_files_only=True,
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
    if torch is not None:
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return True


def score_frame(frame_path: Path) -> FrameDecision:
    """Ask every family and severity question about one frame in one pass."""
    image_module = _require_dependency("PIL.Image", "pillow")
    with image_module.open(frame_path) as opened:
        image = opened.convert("RGB")
    with _LOCK:
        model = _load_model()
        response = model.system_one(None, questions(), images=[image])
    return parse_answers(response)


DETECTOR = DecisionDetector(
    video_model="d1-3b",
    label_prefix="d1",
    model_id=MODEL_ID,
    model_revision=MODEL_REVISION,
    score=score_frame,
)
