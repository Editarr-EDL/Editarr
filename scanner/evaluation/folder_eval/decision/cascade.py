"""`d1-3b-sev+qwen-desc`: d1-3B decides and rates severity, then Qwen3-VL describes.

Two passes over a batch, never both models resident: d1-3b-sev scores every frame
(its own parallel slots), its server is stopped, then the describer (Qwen3-VL-8B on MLX
by default; `EDITARR_CASCADE_DESCRIBER` selects another `qwen3vl-8b*` id) writes a
description only for frames where d1 flagged a family (plain text, or the
`description` field of a one-JSON describer). `run` hands the whole
pending batch to `score_items`, so a run is one d1 pass plus one describer pass.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..describe import DESCRIPTION_ONLY_PROMPT, DESCRIPTION_TOKENS, clip_words
from .chat import THRESHOLD

MODEL_ID = "d1-3b-sev+qwen-desc"
DEFAULT_DESCRIBER = "qwen3vl-8b-mlx"


def describer_id() -> str:
    return os.environ.get("EDITARR_CASCADE_DESCRIBER") or DEFAULT_DESCRIBER


def description_only(backend: Any) -> Any:
    """Turn a qwen_vlm/qwen_mlx backend into a one-turn, no-question describer."""
    backend.spec = dataclasses.replace(
        backend.spec, turns=(DESCRIPTION_ONLY_PROMPT,), max_tokens=DESCRIPTION_TOKENS
    )
    backend.questions = []
    return backend


def described_fields(described: dict) -> dict:
    """`description` and `describe` from a describer's frame output.

    Plain-text describers reply in `transcript`; structured one-JSON describers return
    `description` and their own `describe` status (`ok`, `retried`, or `failed` with a
    null description, kept so describe-eval counts the failure).
    """
    transcript = described.get("transcript")
    if transcript or "description" not in described:
        text = (transcript or [""])[0].strip()
        return {"description": clip_words(text) or "none", "describe": "ok"}
    text = described["description"]
    description = (clip_words(text.strip()) or "none") if isinstance(text, str) else None
    return {"description": description, "describe": described.get("describe") or "ok"}


def flagged(frame: dict) -> bool:
    return any(value >= THRESHOLD for value in frame.get("scores", {}).values())


class CascadeBackend:
    """Composite backend; `score_items` runs both passes over a batch of items."""

    model_id = MODEL_ID
    default_concurrency = 1

    def __init__(
        self,
        decider: Callable[[], Any] | None = None,
        describer: Callable[[], Any] | None = None,
    ):
        from .registry import BACKENDS as backends

        self._decider = decider or backends["d1-3b-sev"]
        self._describer = describer or (lambda: description_only(backends[describer_id()]()))
        self.stats: dict[str, Any] = {}

    def meta(self) -> dict:
        decider = self._decider()
        try:
            meta = decider.meta()
        finally:
            decider.close()
        return {
            **meta,
            "model_id": self.model_id,
            "display_name": f"d1-3B decisions + severity, then {describer_id()} descriptions",
            "runtime": f"{meta.get('runtime')} + {describer_id()}",
            "notes": (
                f"{meta.get('notes', '')} Cascade: frames with any family score >= "
                f"{THRESHOLD} get a plain-text description from {describer_id()}; others "
                "get 'none'. Models run one after the other, never both loaded."
            ),
        }

    def check_available(self) -> None:
        for factory in (self._decider, self._describer):
            backend = factory()
            try:
                check = getattr(backend, "check_available", None)
                if check is not None:
                    check()
            finally:
                backend.close()

    def score_frames(self, paths: list[Path]) -> list[dict]:
        return self.score_items([paths])[0]

    def score_items(self, items: list[list[Path]]) -> list[list[dict]]:
        """d1 pass over every frame, then describer pass over the flagged frames."""
        decider = self._decider()
        try:
            workers = max(1, getattr(decider, "default_concurrency", 1))
            with ThreadPoolExecutor(workers) as pool:
                outputs = list(pool.map(decider.score_frames, items))
        finally:
            decider.close()
        wanted = [
            (i, j, items[i][j])
            for i, frames in enumerate(outputs)
            for j, frame in enumerate(frames)
            if flagged(frame)
        ]
        for frames in outputs:
            for frame in frames:
                frame["description"] = "none"
                frame["describe"] = "skipped"
        if wanted:
            describer = self._describer()
            try:
                for i, j, path in wanted:
                    (described,) = describer.score_frames([path])
                    outputs[i][j].update(described_fields(described))
            finally:
                describer.close()
        self.stats = {"frames": sum(map(len, items)), "described": len(wanted)}
        return outputs

    def close(self) -> None:
        return None
