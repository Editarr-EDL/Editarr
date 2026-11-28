"""Backends for the one-turn JSON ids (`qwen3vl-8b-onejson*`), llama.cpp and MLX.

Prompt, schema and score extraction live in `qwen_onejson`; these classes only change
how a frame is asked, reusing the Qwen3-VL-8B servers/runtimes and spec settings.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scanner.pipeline.qwen_detector import IMAGE_SPAN

from . import qwen_mlx, qwen_vlm
from .chat import TOP_LOGPROBS, first_turn, next_turn
from .qwen_mlx import RETRY_PROMPT, QwenMlxBackend, Step, frame_image
from .qwen_onejson import ONEJSON_TOKENS, frame_from_reply, onejson_prompt, onejson_schema
from .qwen_vlm import QwenVlmBackend
from .schema import image_data_url

# Field order used by the MLX id: the better of the two llama.cpp orders on films_qwen100.
MLX_ORDER = "decide-first"
NOTES = (
    " One user turn, one JSON answer ({order}); booleans scored from the value token's top "
    "log-probabilities (fallback to the generated value is listed in `logprob_fallback`)."
)


class OneJsonVlmBackend(QwenVlmBackend):
    """llama.cpp: the field order is enforced by a JSON-schema grammar."""

    def __init__(self, spec: qwen_vlm.VlmSpec, order: str, models_dir: Path | None = None):
        super().__init__(spec, models_dir)
        self.order = order

    def _score_frame(self, path: Path) -> dict:
        image = image_data_url(path, self.image_long_edge()).split(",", 1)[1]
        prompt = first_turn(self._marker, onejson_prompt(self.order), self.spec.assistant)
        response = self._complete(
            prompt,
            image,
            ONEJSON_TOKENS,
            n_probs=TOP_LOGPROBS,
            json_schema=onejson_schema(self.order),
        )
        tokens = [
            (entry["token"], entry["top_logprobs"])
            for entry in response["completion_probabilities"]
        ]
        return frame_from_reply(tokens)

    def meta(self) -> dict:
        meta = super().meta()
        meta["notes"] = "Generative VLM, temperature 0." + NOTES.format(order=self.order)
        return meta


def generate_steps(runtime: Any, step: Step, max_tokens: int) -> list[tuple[str, list[dict]]]:
    """Greedy generation keeping each step's token text and top log-probabilities."""
    steps: list[tuple[str, list[dict]]] = []
    while len(steps) < max_tokens and not step.is_eos:
        steps.append((runtime.decode([step.token_id]), step.top))
        step = runtime.push(step.token_id)
    return steps


def converse_onejson(runtime: Any, spec: qwen_mlx.MlxVlmSpec, image: Path, order: str) -> dict:
    """MLX: one turn, validated; an invalid reply is retried once in the conversation."""
    prompt = first_turn(IMAGE_SPAN, onejson_prompt(order), spec.assistant)
    steps = generate_steps(runtime, runtime.begin(prompt, frame_image(image)), ONEJSON_TOKENS)
    frame = frame_from_reply(steps)
    if frame["describe"] == "failed":
        retry = runtime.extend(next_turn("", RETRY_PROMPT, spec.assistant))
        frame = frame_from_reply(generate_steps(runtime, retry, ONEJSON_TOKENS), "retried")
    return frame


class OneJsonMlxBackend(QwenMlxBackend):
    def __init__(self, spec: qwen_mlx.MlxVlmSpec, order: str, **kwargs: Any):
        super().__init__(spec, **kwargs)
        self.order = order

    def score_frames(self, paths: list[Path]) -> list[dict]:
        with self._lock:
            if self._runtime is None:
                self.check_available()
                self._runtime = self._runtime_factory(self.model_dir())
            return [converse_onejson(self._runtime, self.spec, Path(p), self.order) for p in paths]

    def meta(self) -> dict:
        meta = super().meta()
        meta["notes"] = "Generative VLM on MLX, greedy." + NOTES.format(order=self.order)
        return meta


def _vlm(model_id: str, order: str, label: str) -> Callable[[], OneJsonVlmBackend]:
    base = qwen_vlm.SPECS["qwen3vl-8b"]
    spec = dataclasses.replace(
        base, model_id=model_id, display_name=f"{base.display_name.split(',')[0]}, {label}"
    )
    return lambda: OneJsonVlmBackend(spec, order)


def _mlx(model_id: str, order: str) -> Callable[[], OneJsonMlxBackend]:
    base = qwen_mlx.SPECS["qwen3vl-8b-mlx"]
    spec = dataclasses.replace(
        base,
        model_id=model_id,
        display_name=f"{base.display_name.split(',')[0]}, one JSON turn ({order})",
    )
    return lambda: OneJsonMlxBackend(spec, order)


BACKENDS: dict[str, Callable[[], Any]] = {
    "qwen3vl-8b-onejson": _vlm("qwen3vl-8b-onejson", "decide-first", "one JSON turn"),
    "qwen3vl-8b-onejson-descfirst": _vlm(
        "qwen3vl-8b-onejson-descfirst", "describe-first", "one JSON turn, description first"
    ),
    "qwen3vl-8b-mlx-onejson": _mlx("qwen3vl-8b-mlx-onejson", MLX_ORDER),
}
