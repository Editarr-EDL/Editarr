"""Generative VLM backends on llama-server: Qwen3.8-9B distill (+ Qwen3.5-9B vision
projector), Qwen3-VL-8B-Instruct and Qwen3.8-27B, each as a direct and a `-seq` id.

Each frame is one raw chat-format conversation sent to `/completion` with the frame as
multimodal data (thinking models get an empty `<think>` block). Every request extends
the previous prompt exactly, so llama.cpp reuses the cached image and earlier turns.
Independent questions cannot share the image prefix: b12556 never checkpoints the
recurrent (Gated DeltaNet) state inside multimodal prompts, so a shorter prefix is
reprocessed in full. The yes/no questions are therefore asked in sequence, each scored
as P(yes) / (P(yes) + P(no)) from the first answer token's top log-probabilities, with
the greedy answer appended before the next question.

`<model>` asks only the five questions; `<model>-seq` first runs four 256-token
description turns and stores them in the frame's `transcript`.
"""

from __future__ import annotations

import dataclasses
import functools
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..backends import BackendUnavailable
from ..describe import (
    DESCRIBE_PROMPT,
    DESCRIBE_SCHEMA,
    DESCRIPTION_TOKENS,
    no_content,
    parse_description,
)
from . import llama_server
from .chat import (
    ASSISTANT,
    ASSISTANT_PLAIN,
    SEQ_TURNS,
    THRESHOLD,
    TOP_LOGPROBS,
    TRANSCRIPT_LIMIT,
    first_turn,
    next_turn,
    vlm_questions,
    yes_no,
    yes_probability,
)
from .llama_server import LlamaServerProcess, runtime_label, server_binary, server_version
from .schema import image_data_url


@dataclass(frozen=True)
class VlmSpec:
    model_id: str
    display_name: str
    repo: str
    model_file: str
    mmproj_repo: str
    mmproj_file: str
    server_args: tuple[str, ...]
    weights_id: str = "qwen38-9b"
    # Generation turns asked before the yes/no questions (empty = questions only).
    turns: tuple[str, ...] = ()
    max_tokens: int = 256
    # Downscale frames to this long edge (0 = as cached); EDITARR_DM_IMAGE_LONG_EDGE overrides.
    image_long_edge: int = 0
    default_concurrency: int = 1
    # Extra JSON description + severity turn: "" (none), "always", or "cond" (only when
    # a yes/no score reaches the 0.5 threshold).
    describe: str = ""
    # Reply prefix opening every assistant turn (empty think block for thinking models).
    assistant: str = ASSISTANT


def _pair(
    base_id: str,
    name: str,
    *,
    direct: tuple[tuple[str, ...], int],
    seq: tuple[tuple[str, ...], int],
    **files: str,
) -> list[VlmSpec]:
    """`<base_id>` (yes/no only) and `<base_id>-seq` (4 turns first) sharing weights.

    `direct` and `seq` are (server args, default concurrency) for each id.
    """
    common = {"weights_id": base_id, **files}
    return [
        VlmSpec(
            model_id=base_id,
            display_name=f"{name}, yes/no",
            server_args=direct[0],
            default_concurrency=direct[1],
            **common,
        ),
        VlmSpec(
            model_id=f"{base_id}-seq",
            display_name=f"{name}, 4 turns then yes/no",
            server_args=seq[0],
            default_concurrency=seq[1],
            turns=SEQ_TURNS,
            **common,
        ),
    ]


SPECS = {
    spec.model_id: spec
    for spec in (
        *_pair(
            "qwen38-9b",
            "Qwen3.8-9B distill (Q8_0) + Qwen3.5-9B mmproj",
            direct=(("-c", "8192", "--parallel", "2", "-fa", "on"), 2),
            seq=(("-c", "16384", "--parallel", "4", "-fa", "on"), 4),
            repo="empero-ai/Qwen3.8-9B-Distill-GGUF",
            model_file="Qwen3.8-9B-Q8_0.gguf",
            mmproj_repo="unsloth/Qwen3.5-9B-GGUF",
            mmproj_file="mmproj-F16.gguf",
        ),
        *_pair(
            "qwen3vl-8b",
            "Qwen3-VL-8B-Instruct (Q8_0, official GGUF)",
            direct=(("-c", "8192", "--parallel", "2", "-fa", "on"), 2),
            seq=(("-c", "16384", "--parallel", "4", "-fa", "on"), 4),
            repo="Qwen/Qwen3-VL-8B-Instruct-GGUF",
            model_file="Qwen3VL-8B-Instruct-Q8_0.gguf",
            mmproj_repo="Qwen/Qwen3-VL-8B-Instruct-GGUF",
            mmproj_file="mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
            assistant=ASSISTANT_PLAIN,
        ),
        # ~15.3 GB of weights + mmproj: one slot with a small context keeps the GPU
        # working set (~18 GB on a 24 GB M4 Pro) within budget; two seq slots swapped.
        *_pair(
            "qwen38-27b",
            "Qwen3.8-27B (UD-IQ4_XS, unsloth GGUF)",
            direct=(("-c", "4096", "--parallel", "1", "-fa", "on"), 1),
            seq=(("-c", "4096", "--parallel", "1", "-fa", "on"), 1),
            repo="unsloth/Qwen3.8-27B-GGUF",
            model_file="Qwen3.8-27B-UD-IQ4_XS.gguf",
            mmproj_repo="unsloth/Qwen3.8-27B-GGUF",
            mmproj_file="mmproj-F16.gguf",
        ),
    )
}


def _describing(base_id: str, describe: str, label: str) -> VlmSpec:
    base = SPECS[base_id]
    return dataclasses.replace(
        base,
        model_id=f"{base_id}-desc" + ("-cond" if describe == "cond" else ""),
        display_name=f"{base.display_name} + {label}",
        describe=describe,
    )


for _spec in (
    _describing("qwen3vl-8b", "always", "JSON description/severity"),
    _describing("qwen3vl-8b", "cond", "JSON description/severity when flagged"),
):
    SPECS[_spec.model_id] = _spec


class QwenVlmBackend:
    """One llama-server per backend; `score_frames` is thread-safe (one slot per frame)."""

    def __init__(self, spec: VlmSpec, models_dir: Path | None = None):
        self.spec = spec
        self.model_id = spec.model_id
        self.default_concurrency = spec.default_concurrency
        self.models_dir = models_dir or llama_server.models_dir()
        self.questions = vlm_questions()
        self._server = LlamaServerProcess(
            self.server_command, self.weights_dir / f"llama-server-{self.model_id}.log"
        )
        self._marker: str | None = None

    @property
    def weights_dir(self) -> Path:
        return self.models_dir / self.spec.weights_id

    @property
    def peak_rss_bytes(self) -> int:
        return self._server.peak_rss_bytes

    def _binary(self) -> str | None:
        return server_binary(self.models_dir, bundled=True)

    @functools.cached_property
    def runtime(self) -> str:
        return runtime_label(server_version(self._binary()))

    def image_long_edge(self) -> int | None:
        value = os.environ.get("EDITARR_DM_IMAGE_LONG_EDGE")
        edge = int(value) if value else self.spec.image_long_edge
        return edge or None

    def meta(self) -> dict:
        turns = (
            f" After {len(self.spec.turns)} description turns (≤{self.spec.max_tokens} tokens"
            " each, saved as `transcript`)."
            if self.spec.turns
            else ""
        )
        return {
            "model_id": self.model_id,
            "display_name": self.spec.display_name,
            "supports": sorted(question.key for question in self.questions),
            "thresholds": {"*": 0.5},
            "image_long_edge": self.image_long_edge(),
            "runtime": f"{self.runtime} llama-server /completion",
            "artifact": (
                f"{self.spec.repo}: {self.spec.model_file} + "
                f"{self.spec.mmproj_repo}: {self.spec.mmproj_file}"
            ),
            "notes": (
                "Generative VLM, thinking disabled, temperature 0. Yes/no questions asked in "
                "sequence in one conversation; score = P(yes)/(P(yes)+P(no)) from the first "
                f"answer token's top {TOP_LOGPROBS} log-probabilities.{turns}"
            ),
        }

    def score_frames(self, paths: list[Path]) -> list[dict]:
        self.check_available()
        self._server.ensure_started()
        if self._marker is None:
            self._marker = self._server.get("/props")["media_marker"]
        results = [self._score_frame(Path(path)) for path in paths]
        self._server.sample_rss()
        return results

    def _score_frame(self, path: Path) -> dict:
        image = image_data_url(path, self.image_long_edge()).split(",", 1)[1]
        prompt = ""
        transcript: list[str] = []
        reply = ""
        for text in self.spec.turns:
            prompt += self._turn(prompt, reply, text)
            reply = self._complete(prompt, image, self.spec.max_tokens)["content"]
            transcript.append(reply[:TRANSCRIPT_LIMIT])
        scores: dict[str, float] = {}
        for question in self.questions:
            text = yes_no(question)
            prompt += self._turn(prompt, reply, text)
            response = self._complete(prompt, image, 1, n_probs=TOP_LOGPROBS)
            scores[question.key] = yes_probability(
                response["completion_probabilities"][0]["top_logprobs"]
            )
            reply = response["content"]
        frame = {"scores": scores, "labels": [], "asked": sorted(scores)}
        if self.spec.describe:
            frame.update(self._describe(prompt, reply, image, scores))
        if transcript:
            frame["transcript"] = transcript
        return frame

    def _turn(self, prompt: str, reply: str, text: str) -> str:
        if not prompt:
            return first_turn(self._marker, text, self.spec.assistant)
        return next_turn(reply, text, self.spec.assistant)

    def _describe(self, prompt: str, reply: str, image: str, scores: dict) -> dict:
        """Description + severity JSON turn, constrained by DESCRIBE_SCHEMA."""
        if self.spec.describe == "cond" and max(scores.values(), default=0.0) < THRESHOLD:
            description, severity = no_content()
            return {"description": description, "severity": severity, "describe": "skipped"}
        prompt += self._turn(prompt, reply, DESCRIBE_PROMPT)
        text = self._complete(prompt, image, DESCRIPTION_TOKENS, json_schema=DESCRIBE_SCHEMA)[
            "content"
        ]
        try:
            description, severity = parse_description(text)
        except ValueError:
            return {"description": None, "severity": None, "describe": "failed"}
        return {"description": description, "severity": severity, "describe": "ok"}

    def _complete(
        self,
        prompt: str,
        image: str,
        n_predict: int,
        n_probs: int = 0,
        json_schema: dict | None = None,
    ) -> dict:
        body = {
            "prompt": {"prompt_string": prompt, "multimodal_data": [image]},
            "n_predict": n_predict,
            "temperature": 0,
            "cache_prompt": True,
        }
        if n_probs:
            body["n_probs"] = n_probs
        if json_schema is not None:
            body["json_schema"] = json_schema
        try:
            return self._server.post("/completion", body)
        except RuntimeError as exc:
            raise RuntimeError(f"{self.model_id}: {exc}") from exc

    def close(self) -> None:
        self._server.close()

    def server_command(self, port: int) -> list[str]:
        return [
            self._binary() or "llama-server",
            "-m",
            str(self.weights_dir / self.spec.model_file),
            "--mmproj",
            str(self.weights_dir / self.spec.mmproj_file),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            "999",
            "--no-webui",
            *self.spec.server_args,
        ]

    def check_available(self) -> None:
        if self._binary() is None:
            raise BackendUnavailable(
                "unavailable: llama-server not found or not executable (unpack a llama.cpp "
                "release under the models dir or set EDITARR_LLAMA_SERVER)"
            )
        missing = [
            (repo, name)
            for repo, name in (
                (self.spec.repo, self.spec.model_file),
                (self.spec.mmproj_repo, self.spec.mmproj_file),
            )
            if not (self.weights_dir / name).is_file()
        ]
        if missing:
            commands = "; ".join(
                f"uvx --from huggingface_hub hf download {repo} {name} "
                f"--local-dir {self.weights_dir}"
                for repo, name in missing
            )
            raise BackendUnavailable(
                f"unavailable: missing weights in {self.weights_dir}; download with `{commands}`"
            )


def _factory(model_id: str) -> Callable[[], QwenVlmBackend]:
    return lambda: QwenVlmBackend(SPECS[model_id])


BACKENDS: dict[str, Callable[[], QwenVlmBackend]] = {
    model_id: _factory(model_id) for model_id in SPECS
}
