"""MLX (mlx-vlm) variants of the generative VLM backends in `qwen_vlm`.

Same conversation as the llama.cpp ids: identical turn strings (`first_turn`/`next_turn`),
the four description turns for `-seq`, then the five yes/no questions chained in one
conversation, each scored with `yes_probability` over the first answer token's top 20
log-probabilities (temperature 0). The frame is encoded once: only the first call passes
pixel values; every later turn feeds just its new text tokens into the same KV cache.
`MlxRuntime` reuses the shipped Qwen3-VL detector's `QwenConversation` for that cache.

One model is loaded lazily per backend, scored one frame at a time (concurrency 1), and
released in `close()`.
"""

from __future__ import annotations

import dataclasses
import gc
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scanner.pipeline.qwen_detector import IMAGE_SPAN, QwenConversation

from ..backends import BackendUnavailable
from ..describe import DESCRIBE_PROMPT, DESCRIPTION_TOKENS, no_content, parse_description
from .chat import (
    ASSISTANT,
    ASSISTANT_PLAIN,
    SEQ_TURNS,
    THRESHOLD,
    TRANSCRIPT_LIMIT,
    first_turn,
    next_turn,
    vlm_questions,
    yes_no,
    yes_probability,
)


@dataclass(frozen=True)
class MlxVlmSpec:
    model_id: str
    display_name: str
    # Editarr artifact id (`acquire_artifact`) or a Hugging Face repo id.
    artifact: str | None
    repo: str | None
    assistant: str
    turns: tuple[str, ...] = ()
    max_tokens: int = 256
    # Extra JSON description + severity turn: "" (none), "always", or "cond".
    describe: str = ""


def _pair(base_id: str, name: str, **source: Any) -> list[MlxVlmSpec]:
    return [
        MlxVlmSpec(model_id=base_id, display_name=f"{name}, yes/no", **source),
        MlxVlmSpec(
            model_id=f"{base_id}-seq",
            display_name=f"{name}, 4 turns then yes/no",
            turns=SEQ_TURNS,
            **source,
        ),
    ]


SPECS: dict[str, MlxVlmSpec] = {
    spec.model_id: spec
    for spec in (
        *_pair(
            "qwen3vl-8b-mlx",
            "Qwen3-VL-8B-Instruct (MLX 4-bit, Editarr artifact)",
            artifact="qwen3-vl-8b-instruct-4bit",
            repo=None,
            assistant=ASSISTANT_PLAIN,
        ),
        *_pair(
            "qwen38-27b-mlx",
            "Qwen3.8-27B (MLX 4-bit, lmstudio-community)",
            artifact=None,
            repo="lmstudio-community/Qwen3.8-27B-MLX-4bit",
            assistant=ASSISTANT,
        ),
    )
}


for _describe, _suffix in (("always", "-desc"), ("cond", "-desc-cond")):
    _base = SPECS["qwen3vl-8b-mlx"]
    SPECS[f"{_base.model_id}{_suffix}"] = dataclasses.replace(
        _base,
        model_id=f"{_base.model_id}{_suffix}",
        display_name=f"{_base.display_name} + JSON description/severity"
        + (" when flagged" if _describe == "cond" else ""),
        describe=_describe,
    )


def image_long_edge() -> int | None:
    """Downscale edge shared with the llama.cpp ids (default: frames as cached)."""
    value = os.environ.get("EDITARR_DM_IMAGE_LONG_EDGE")
    return int(value) or None if value else None


def frame_image(path: Path) -> Any:
    """The frame path, or a downscaled in-memory copy when an edge is configured."""
    edge = image_long_edge()
    if not edge:
        return path
    from PIL import Image

    with Image.open(path) as image:
        if max(image.size) <= edge:
            return path
        image = image.convert("RGB")
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        return image


@dataclass
class Step:
    """Model output after the last fed token: greedy next token and top log-probabilities."""

    token_id: int
    is_eos: bool
    top: list[dict]


class MlxRuntime(QwenConversation):
    """One loaded mlx-vlm model and a single conversation's KV cache.

    Extends the shipped detector's conversation with end-of-turn detection, decoding and
    unloading, so free-text turns can be generated in the same cache.
    """

    def __init__(self, model_dir: Path):
        import mlx_vlm

        model, processor = mlx_vlm.load(str(model_dir))
        super().__init__(model, processor)
        eos = {self.tokenizer.eos_token_id}
        for token in ("<|im_end|>", "<|endoftext|>"):
            token_id = self.tokenizer.convert_tokens_to_ids(token)
            if isinstance(token_id, int) and token_id >= 0:
                eos.add(token_id)
        self.eos = {token_id for token_id in eos if token_id is not None}

    def _forward(self, input_ids, inputs_embeds=None) -> Step:
        step = super()._forward(input_ids, inputs_embeds)
        top = [{"token": token, "logprob": logprob} for token, logprob in step.top]
        return Step(token_id=step.token_id, is_eos=step.token_id in self.eos, top=top)

    def decode(self, ids: list[int]) -> str:
        return self.tokenizer.decode(ids)

    def close(self) -> None:
        self.end()
        self.model = self.processor = self.tokenizer = None
        gc.collect()
        self._mx.clear_cache()


RETRY_PROMPT = "That reply was not valid JSON. Reply with only the JSON object."


def generate(runtime: Any, step: Step, max_tokens: int) -> str:
    """Greedy-generate from `step`, feeding every token into the cache; returns the text."""
    generated: list[int] = []
    while len(generated) < max_tokens and not step.is_eos:
        generated.append(step.token_id)
        step = runtime.push(step.token_id)
    return runtime.decode(generated)


def converse(runtime: Any, spec: MlxVlmSpec, image: Path, questions) -> dict:
    """Run one frame's conversation on `runtime` (begin/extend/push/decode)."""

    def turn(first: bool, text: str) -> Step:
        if first:
            return runtime.begin(first_turn(IMAGE_SPAN, text, spec.assistant), frame_image(image))
        return runtime.extend(next_turn("", text, spec.assistant))

    transcript: list[str] = []
    started = False
    for text in spec.turns:
        step = turn(not started, text)
        started = True
        transcript.append(generate(runtime, step, spec.max_tokens)[:TRANSCRIPT_LIMIT])
    scores: dict[str, float] = {}
    for index, question in enumerate(questions):
        step = turn(not started, yes_no(question))
        started = True
        scores[question.key] = yes_probability(step.top)
        if index < len(questions) - 1 or spec.describe:
            runtime.push(step.token_id)  # keep the greedy answer in the conversation
    frame = {"scores": scores, "labels": [], "asked": sorted(scores)}
    if transcript:
        frame["transcript"] = transcript
    if spec.describe:
        frame.update(describe(runtime, spec, scores, turn))
    return frame


def describe(runtime: Any, spec: MlxVlmSpec, scores: dict, turn) -> dict:
    """JSON description + severity turn; an invalid reply is retried once in-conversation."""
    if spec.describe == "cond" and max(scores.values(), default=0.0) < THRESHOLD:
        description, severity = no_content()
        return {"description": description, "severity": severity, "describe": "skipped"}
    status = "ok"
    for prompt in (DESCRIBE_PROMPT, RETRY_PROMPT):
        text = generate(runtime, turn(False, prompt), DESCRIPTION_TOKENS)
        try:
            description, severity = parse_description(text)
        except (ValueError, TypeError):
            status = "retried"
            continue
        return {"description": description, "severity": severity, "describe": status}
    return {"description": None, "severity": None, "describe": "failed"}


class QwenMlxBackend:
    """mlx-vlm backend; loads on first use, scores one frame at a time."""

    default_concurrency = 1

    def __init__(self, spec: MlxVlmSpec, runtime_factory: Callable[[Path], Any] = MlxRuntime):
        self.spec = spec
        self.model_id = spec.model_id
        self.questions = vlm_questions()
        self._runtime_factory = runtime_factory
        self._runtime: Any = None
        self._lock = threading.Lock()

    @property
    def runtime(self) -> str:
        from importlib.metadata import version

        return f"mlx-vlm {version('mlx-vlm')}"

    def model_dir(self) -> Path:
        """Local model directory; raises BackendUnavailable when it is not downloaded."""
        if self.spec.artifact:
            from scanner.model_acquisition import acquire_artifact

            try:
                return Path(acquire_artifact(self.spec.artifact))
            except Exception as exc:  # noqa: BLE001 - any acquisition failure disables the model
                raise BackendUnavailable(f"unavailable: {self.spec.artifact}: {exc}") from exc
        from huggingface_hub import snapshot_download

        try:
            return Path(snapshot_download(self.spec.repo, local_files_only=True))
        except Exception as exc:  # noqa: BLE001 - not cached locally
            raise BackendUnavailable(
                f"unavailable: {self.spec.repo} is not downloaded; fetch it with "
                f"`uvx --from huggingface_hub hf download {self.spec.repo}` "
                "(keep at least 40 GB free)"
            ) from exc

    def check_available(self) -> None:
        try:
            import mlx_vlm  # noqa: F401
        except ImportError as exc:
            raise BackendUnavailable(f"unavailable: mlx-vlm is not installed ({exc})") from exc
        self.model_dir()

    def meta(self) -> dict:
        source = self.spec.repo or f"Editarr artifact {self.spec.artifact}"
        turns = " After 4 description turns (saved as `transcript`)." if self.spec.turns else ""
        return {
            "model_id": self.model_id,
            "display_name": self.spec.display_name,
            "supports": sorted(question.key for question in self.questions),
            "thresholds": {"*": 0.5},
            "image_long_edge": image_long_edge(),
            "runtime": self.runtime,
            "artifact": source,
            "notes": (
                "Generative VLM on MLX, thinking disabled, greedy. Same prompts, chaining and "
                f"scoring as the llama.cpp ids; frame encoded once per conversation.{turns}"
            ),
        }

    def score_frames(self, paths: list[Path]) -> list[dict]:
        with self._lock:
            if self._runtime is None:
                self.check_available()
                self._runtime = self._runtime_factory(self.model_dir())
            return [
                converse(self._runtime, self.spec, Path(path), self.questions) for path in paths
            ]

    def close(self) -> None:
        with self._lock:
            if self._runtime is not None:
                self._runtime.close()
                self._runtime = None


def _factory(model_id: str) -> Callable[[], QwenMlxBackend]:
    return lambda: QwenMlxBackend(SPECS[model_id])


BACKENDS: dict[str, Callable[[], QwenMlxBackend]] = {
    model_id: _factory(model_id) for model_id in SPECS
}
