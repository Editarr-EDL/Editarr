"""Qwen3-VL summaries for video frames flagged by a safety detector."""

from __future__ import annotations

import importlib
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from scanner.evaluation.subcategorization import (
    QwenSubcategorization,
    SubcategorizationResult,
    subcategorize_qwen_response,
)
from scanner.model_acquisition import acquire_artifact
from scanner.models import MCF_CATEGORY_REGISTRY, McfCategoryFamily
from scanner.settings.capabilities import (
    DEFAULT_QWEN_MODEL,
    SUPPORTED_QWEN_MODELS,
    require_available,
)

QWEN_MODELS = {
    DEFAULT_QWEN_MODEL: (
        "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "defcdea7cc7a4b0858fea563cbbce171d328e457",
    ),
    "qwen3-vl-4b-instruct-4bit": (
        "mlx-community/Qwen3-VL-4B-Instruct-4bit",
        "2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b",
    ),
}
PROMPT = (
    "Analyze these sampled frames from one flagged video segment for potentially "
    "objectionable content. Identify any nudity, sexual activity, immodesty, violence, "
    "blood/gore, drug use (including alcohol, prescription medications, tobacco and "
    "cigarettes/cigars, syringes, and other paraphernalia), weapons, or other dangerous "
    "content. For each category detected, briefly describe what is objectionable and why."
    "Do not include any restatement of just the category name in the description. "
    "Explain only what is visibly objectionable across any of the frames that led to the "
    "classification. 'Nudity' is defined as the exposure of genitalia, buttocks, or female "
    "breasts or nipples."
    "Immodesty is defined for females as the exposure of any other private body parts such "
    "as the upper thigh, lower abdomen, a significant portion of the back, the exposure or "
    "accentuating of female breasts partially covered by clothing (such as cleavage), but "
    "not including fully covered areas."
    "Male subjects can be considered immodest if they are wearing clothing that is very "
    "revealing or accentuates the upper thighs or buttocks or genital area, such as a thong "
    "or speedo."
    "Male subjects can also be considered immodest if their chest is exposed, such as in a "
    "shirtless or topless scene."
    "The gender of the subjects should be noted if visible and nudity, immodesty, or"
    "sexual activity is present. Tell which body parts are exposed if nudity is detected. "
    "You do not need to describe the appearance of the subjects, such as their clothing or "
    "hair or facial features."
    "Breasts should be expressly noted and not just lumped into generic terms like 'chest' "
    "or 'torso', as should buttocks not be "
    "lumped in with 'thighs' if clearly seen from a rear or side view where the split is "
    "obvious. 'Thighs' may be used to describe the upper leg area if the buttocks are not "
    "visible. "
    "If sexual activity is detected, explain what is happening and what body parts are involved. "
    "Explain what type of weapon is present if weapons are detected. If blood or gore is "
    "detected, explain what "
    "if any action is happening and what body parts are affected and what if any weapon is "
    "involved. "
    "If drugs or drug paraphernalia are detected, explain what type of drug is present and "
    "what paraphernalia is involved. "
    "Do not mention what is not visible, make assumptions, describe anything that is visible "
    "but not objectionable including lighting and scenery or the background, "
    "repeat details, mention the frames, or be overly descriptive. Keep it very concise; "
    "complete sentences are not required. If no objectionable "
    "content is detected, return 'no objectionable content' as the only category and the "
    "only description. "
)
_CATEGORY_PATTERNS = (
    ("nudity", (r"\bnudity\b", r"\bnude\b", r"\bnaked\b")),
    ("sexual activity", (r"\bsexual activity\b", r"\bsexual\b", r"\bsex\b")),
    ("violence", (r"\bviolence\b", r"\bviolent\b", r"\bassault\b", r"\bfight\b")),
    ("blood/gore", (r"\bblood\b", r"\bgore\b", r"\bgraphic injury\b")),
    ("drug use", (r"\bdrug use\b", r"\bdrugs?\b", r"\bnarcotics?\b")),
    ("weapons", (r"\bweapons?\b", r"\bgun\b", r"\bknife\b", r"\bfirearm\b")),
    ("other dangerous content", (r"\bdangerous content\b", r"\bdanger\b")),
)
_CACHE: dict[str, object] = {}
_LOAD_LOCK = Lock()


@dataclass(frozen=True)
class QwenSummary:
    categories: tuple[str, ...]
    explanation: str
    subcategorization: tuple[SubcategorizationResult, ...] = ()
    valid_subcategorization_output: bool = False

    @property
    def group_key(self) -> str:
        return "|".join(self.categories)


def subcategorization_prompt(
    families: tuple[McfCategoryFamily, ...], *, base_prompt: str = PROMPT
) -> str:
    """Constrain Qwen output to exact canonical tokens for known detector families."""
    requested_families = tuple(dict.fromkeys(families))
    if not requested_families:
        return base_prompt
    allowed = "\n".join(
        f"- {family.value}: {', '.join(token for token, _label in MCF_CATEGORY_REGISTRY[family])}"
        for family in requested_families
    )
    return (
        f"{base_prompt}\n\n"
        "Return exactly one JSON object and no markdown or surrounding text. The object must "
        "have exactly these keys: version, objectionable, summary, classifications. Set "
        "version to 1, objectionable to a Boolean, and summary to a concise non-empty string. "
        "If objectionable is false, classifications must be an empty array. If objectionable "
        "is true, classifications must contain exactly one object for every family below. "
        "Each classification object must have exactly these keys: family, subcategory, "
        "confidence, evidence. Confidence must be a number from 0 through 1. Evidence must be "
        "a concise non-empty description of visible evidence. Use only the exact family and "
        f"subcategory tokens listed here:\n{allowed}"
    )


def _require_dependency(import_name: str, install_hint: str):
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Qwen3-VL video summaries require optional dependency "
            f"'{install_hint}'. Install the stack dependencies first."
        ) from exc


def _load_model(qwen_model: str = DEFAULT_QWEN_MODEL):
    """Acquire and cache the selected Qwen model and processor for frame summaries."""
    require_available("qwen3_vl")
    if qwen_model not in SUPPORTED_QWEN_MODELS:
        raise ValueError(f"Unsupported Qwen model: {qwen_model}")
    if qwen_model in _CACHE:
        return _CACHE[qwen_model]

    with _LOAD_LOCK:
        if qwen_model in _CACHE:
            return _CACHE[qwen_model]

        if platform.system() != "Darwin":
            raise RuntimeError(
                "Qwen3-VL video summaries use MLX and require a Mac running macOS."
            )

        mlx_vlm = _require_dependency("mlx_vlm", "mlx-vlm")
        mlx_vlm_utils = _require_dependency("mlx_vlm.utils", "mlx-vlm")
        model_dir = acquire_artifact(qwen_model)
        model, processor = mlx_vlm.load(str(model_dir))
        config = mlx_vlm_utils.load_config(str(model_dir))
        _CACHE[qwen_model] = (model, processor, config)
        return _CACHE[qwen_model]


def _categories_from_response(response: str) -> tuple[str, ...]:
    """Extract supported content categories from a model summary response."""
    lowered = response.lower()
    categories = tuple(
        category
        for category, patterns in _CATEGORY_PATTERNS
        if any(re.search(pattern, lowered) for pattern in patterns)
    )
    if categories:
        return categories
    if re.search(
        r"\b(no|none|without)\b.{0,60}\b(objectionable|unsafe|explicit|violent)\b",
        lowered,
    ):
        return ("no objectionable content",)
    return ("other dangerous content",)


def _summarize(
    prompt: str,
    *,
    image_paths: list[Path],
    families: tuple[McfCategoryFamily, ...] = (),
    qwen_model: str = DEFAULT_QWEN_MODEL,
) -> QwenSummary:
    """Run a Qwen prompt against one or more video-frame images."""
    # Admission and the visual stage normally validate this first. Keep the public
    # summary/replay path defensive too, and reject before importing the MLX stack.
    require_available("qwen3_vl")
    mlx_vlm = _require_dependency("mlx_vlm", "mlx-vlm")
    prompt_utils = _require_dependency("mlx_vlm.prompt_utils", "mlx-vlm")
    if qwen_model not in SUPPORTED_QWEN_MODELS:
        raise ValueError(f"Unsupported Qwen model: {qwen_model}")
    model, processor, config = _load_model(qwen_model)
    effective_prompt = subcategorization_prompt(families, base_prompt=prompt)
    formatted_prompt = prompt_utils.apply_chat_template(
        processor,
        config,
        effective_prompt,
        num_images=len(image_paths),
    )
    generate_kwargs = {
        "max_tokens": 160,
        "temperature": 0.0,
        "verbose": False,
    }
    generate_kwargs["image"] = [str(image_path.resolve()) for image_path in image_paths]
    result = mlx_vlm.generate(
        model,
        processor,
        formatted_prompt,
        **generate_kwargs,
    )
    response = result.text.strip()
    if not response:
        response = "No summary was returned by the classifier."
    parsed: QwenSubcategorization | None = None
    if families:
        model_id, model_revision = QWEN_MODELS[qwen_model]
        parsed = subcategorize_qwen_response(
            response,
            families,
            model_id=model_id,
            model_revision=model_revision,
        )
    if parsed is None or not parsed.valid_output:
        return QwenSummary(
            categories=_categories_from_response(response),
            explanation=response,
            subcategorization=parsed.results if parsed else (),
            valid_subcategorization_output=False,
        )
    categories = (
        ("no objectionable content",)
        if not parsed.objectionable
        else tuple(value.value for value in parsed.reported_subcategories)
    )
    return QwenSummary(
        categories=categories,
        explanation=parsed.explanation,
        subcategorization=parsed.results,
        valid_subcategorization_output=True,
    )


def summarize_frames(
    frame_paths: list[Path],
    *,
    prompt: str = PROMPT,
    families: tuple[McfCategoryFamily, ...] = (),
    qwen_model: str = DEFAULT_QWEN_MODEL,
) -> QwenSummary:
    """Summarize representative frames from one flagged video segment together."""
    if not frame_paths:
        raise ValueError("At least one frame is required")
    return _summarize(
        prompt,
        image_paths=frame_paths,
        families=families,
        qwen_model=qwen_model,
    )
