"""Qwen yes/no video detectors on MLX (mlx-vlm), Apple silicon only.

Each frame is one conversation: the image is encoded once with the first question,
then every later family question feeds only its new text tokens into the same
cache. A family's score is P(yes) / (P(yes) + P(no)) over the first answer token's top
log-probabilities at temperature 0, and the greedy answer token stays in the
conversation before the next question. Qwen3.8 uses its non-thinking assistant
prefix for each decision.

The model is loaded through :mod:`scanner.pipeline.qwen_summary`, so a scan whose
Severity and Description model is the same artifact reuses one loaded instance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

from scanner.modeling.model_acquisition_manifest import manifest
from scanner.settings.capabilities import (
    QWEN38_27B_MODEL,
    QWEN38_27B_VIDEO_MODEL,
    require_available,
)

from .decision_detection import (
    DETECTED_FAMILIES,
    DecisionDetector,
    FrameDecision,
    family_question,
)
from .dependencies import require_dependency

ARTIFACTS = {
    "qwen3-vl-8b": "qwen3-vl-8b-instruct-4bit",
    "qwen3-vl-4b": "qwen3-vl-4b-instruct-4bit",
    QWEN38_27B_VIDEO_MODEL: QWEN38_27B_MODEL,
}
# Start Qwen3.8 at the Qwen3-VL threshold until labeled footage supports calibration.
THRESHOLDS = {family: 0.5 for family in DETECTED_FAMILIES}
TOP_LOGPROBS = 20
USER = "<|im_start|>user\n"
END = "<|im_end|>\n"
ASSISTANT = "<|im_start|>assistant\n"
NON_THINKING_ASSISTANT = ASSISTANT + "<think>\n\n</think>\n\n"
# Qwen chat templates render an image as this span; the processor expands the pad token.
IMAGE_SPAN = "<|vision_start|><|image_pad|><|vision_end|>"
# Prompt-time model kwargs that stay valid for text-only continuations.
_CONTINUE_KWARGS = ("rope_deltas",)
_LOCK = Lock()


def first_turn(text: str, assistant: str = ASSISTANT) -> str:
    """Opening user turn: the frame followed by ``text``, then the reply prefix."""
    return f"{USER}{IMAGE_SPAN}{text}{END}{assistant}"


def next_turn(text: str, assistant: str = ASSISTANT) -> str:
    """Close the previous reply and open the next user turn and reply prefix."""
    return f"{END}{USER}{text}{END}{assistant}"


def yes_no(family) -> str:
    return f"{family_question(family)} Answer yes or no."


def yes_probability(top: list[tuple[str, float]]) -> float:
    """P(yes) / (P(yes) + P(no)) over first-token candidates such as "Yes" or " no";
    0.0 when neither answer is among them."""
    totals = {"yes": 0.0, "no": 0.0}
    for token, logprob in top:
        word = token.strip().strip(".,!").lower()
        if word in totals:
            totals[word] += math.exp(logprob)
    total = totals["yes"] + totals["no"]
    return totals["yes"] / total if total > 0 else 0.0


@dataclass
class Step:
    """Greedy next token after the last fed token, with its top log-probabilities."""

    token_id: int
    top: list[tuple[str, float]]


class QwenConversation:
    """One frame conversation over a loaded mlx-vlm Qwen model and its prompt cache."""

    def __init__(self, model, processor):
        self.model = model
        self.processor = processor
        self.tokenizer = getattr(processor, "tokenizer", processor)
        self._mx = require_dependency("mlx.core", "mlx", "The Qwen video detector requires")
        self._cache: list | None = None
        self._kwargs: dict = {}

    def _reset_positions(self) -> None:
        # The language model keeps M-RoPE state from the last prompt; clear it so neither this
        # conversation nor a later summary inherits stale positions.
        language = self.model.language_model
        for attribute in ("_rope_deltas", "_position_ids"):
            if hasattr(language, attribute):
                setattr(language, attribute, None)

    def begin(self, prompt: str, image: Path | object) -> Step:
        """Encode the frame and prompt into a fresh cache.

        ``image`` is a frame path or an already decoded (e.g. downscaled) PIL image.
        """
        utils = require_dependency("mlx_vlm.utils", "mlx-vlm", "The Qwen video detector requires")
        cache = require_dependency(
            "mlx_vlm.models.cache", "mlx-vlm", "The Qwen video detector requires"
        )
        self._reset_positions()
        inputs = utils.prepare_inputs(
            self.processor,
            images=[str(image) if isinstance(image, Path) else image],
            prompts=[prompt],
            image_token_index=getattr(self.model.config, "image_token_index", None),
        )
        input_ids = inputs["input_ids"]
        data = {
            key: value
            for key, value in inputs.items()
            if key not in ("input_ids", "pixel_values", "attention_mask")
        }
        embedded = self.model.get_input_embeddings(
            input_ids, inputs.get("pixel_values"), mask=inputs.get("attention_mask"), **data
        )
        self._kwargs = {
            **data,
            **{
                key: value
                for key, value in embedded.to_dict().items()
                if key != "inputs_embeds" and value is not None
            },
        }
        # MLX defers to the model's cache factory, including Qwen3.8's recurrent layers.
        self._cache = cache.make_prompt_cache(self.model.language_model)
        step = self._forward(input_ids, embedded.inputs_embeds)
        # Later turns are text only: positions continue from the cache offset plus the
        # prompt's M-RoPE delta, so full-prompt position ids and masks must be dropped.
        self._kwargs = {
            key: value for key, value in self._kwargs.items() if key in _CONTINUE_KWARGS
        }
        return step

    def extend(self, text: str) -> Step:
        """Feed more text (no image) into the cache."""
        ids = self.tokenizer.encode(text, add_special_tokens=False)
        return self._forward(self._mx.array([ids]))

    def push(self, token_id: int) -> Step:
        return self._forward(self._mx.array([[token_id]]))

    def end(self) -> None:
        """Release the conversation's cache and position state."""
        self._cache = None
        self._kwargs = {}
        self._reset_positions()

    def _forward(self, input_ids, inputs_embeds=None) -> Step:
        mx = self._mx
        outputs = self.model.language_model(
            input_ids, inputs_embeds=inputs_embeds, cache=self._cache, **self._kwargs
        )
        logits = outputs.logits[0, -1, :].astype(mx.float32)
        logprobs = logits - mx.logsumexp(logits)
        order = mx.argsort(-logprobs)[:TOP_LOGPROBS]
        mx.eval(order, logprobs)
        ids = order.tolist()
        values = logprobs[order].tolist()
        top = [
            (self.tokenizer.decode([token_id]), value)
            for token_id, value in zip(ids, values, strict=True)
        ]
        return Step(token_id=ids[0], top=top)


def converse(conversation, image: Path, *, assistant: str = ASSISTANT) -> dict:
    """Ask every family question about one frame in a single chained conversation."""
    scores = {}
    try:
        for index, family in enumerate(DETECTED_FAMILIES):
            text = yes_no(family)
            step = (
                conversation.begin(first_turn(text, assistant), image)
                if index == 0
                else conversation.extend(next_turn(text, assistant))
            )
            scores[family] = yes_probability(step.top)
            if index < len(DETECTED_FAMILIES) - 1:
                conversation.push(step.token_id)  # keep the greedy answer in the conversation
    finally:
        conversation.end()
    return scores


def _conversation(video_model: str) -> QwenConversation:
    """Load (or reuse) the pinned Qwen artifact shared with the description step."""
    require_available(video_model)
    from .qwen_summary import _load_model

    model, processor, _config = _load_model(ARTIFACTS[video_model])
    return QwenConversation(model, processor)


def score_frame(video_model: str, frame_path: Path) -> FrameDecision:
    """Score one frame with the selected Qwen detector's answer prefix."""
    assistant = NON_THINKING_ASSISTANT if video_model == QWEN38_27B_VIDEO_MODEL else ASSISTANT
    with _LOCK:
        scores = converse(_conversation(video_model), frame_path, assistant=assistant)
    return FrameDecision(scores=scores, thresholds=THRESHOLDS)


@lru_cache(maxsize=None)
def detector(video_model: str) -> DecisionDetector:
    """Describe one Qwen detector id; weights load on the first scored frame."""
    artifact = manifest().artifact(ARTIFACTS[video_model])
    return DecisionDetector(
        video_model=video_model,
        label_prefix="qwen3_8" if video_model == QWEN38_27B_VIDEO_MODEL else "qwen3_vl",
        model_id=artifact.repository or artifact.id,
        model_revision=artifact.revision or artifact.backend_version,
        score=lambda frame_path: score_frame(video_model, frame_path),
    )
