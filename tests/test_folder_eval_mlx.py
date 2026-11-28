"""Tests for the MLX VLM backends with a mocked runtime (no model is loaded)."""

from __future__ import annotations

import math

import pytest

from scanner.evaluation.folder_eval.backends import BackendUnavailable
from scanner.evaluation.folder_eval.decision.chat import (
    ASSISTANT,
    ASSISTANT_PLAIN,
    TRANSCRIPT_LIMIT,
    vlm_questions,
)
from scanner.evaluation.folder_eval.decision.qwen_mlx import (
    SPECS,
    QwenMlxBackend,
    Step,
    converse,
    frame_image,
)
from scanner.pipeline.qwen_detector import IMAGE_SPAN

EOS = 99


class FakeRuntime:
    """Records calls; yes/no turns answer with P(yes) from `p_yes`, generation emits
    `gen_length` tokens then EOS (or never stops when `gen_length` is None)."""

    def __init__(self, p_yes=None, gen_length=None):
        self.p_yes = p_yes or {}
        self.gen_length = gen_length
        self.calls: list[tuple] = []
        self.generated = 0
        self.closed = False

    def _step(self, text: str) -> Step:
        if "Answer yes or no." in text:
            p = next((v for k, v in self.p_yes.items() if k in text), 0.2)
            return Step(
                1,
                False,
                [
                    {"token": "Yes", "logprob": math.log(p)},
                    {"token": "No", "logprob": math.log(1 - p)},
                ],
            )
        self.generated = 0
        return Step(7, False, [])

    def begin(self, prompt, image):
        self.calls.append(("begin", prompt, image))
        return self._step(prompt)

    def extend(self, text):
        self.calls.append(("extend", text))
        return self._step(text)

    def push(self, token_id):
        self.calls.append(("push", token_id))
        self.generated += 1
        done = self.gen_length is not None and self.generated >= self.gen_length
        return Step(EOS if done else 7, done, [])

    def decode(self, ids):
        return "w" * (len(ids) * 20)

    def close(self):
        self.closed = True


def test_direct_conversation_encodes_the_frame_once_and_chains_questions(tmp_path):
    runtime = FakeRuntime({"violence?": 0.9})
    frame = tmp_path / "f.jpg"

    result = converse(runtime, SPECS["qwen3vl-8b-mlx"], frame, vlm_questions())

    kinds = [call[0] for call in runtime.calls]
    assert kinds == [
        "begin",
        "push",
        "extend",
        "push",
        "extend",
        "push",
        "extend",
        "push",
        "extend",
    ]
    begin = runtime.calls[0]
    assert begin[1].startswith(f"<|im_start|>user\n{IMAGE_SPAN}Does this video frame show drugs")
    assert begin[1].endswith(ASSISTANT_PLAIN) and begin[2] == frame
    assert runtime.calls[2][1] == (
        "<|im_end|>\n<|im_start|>user\nDoes this video frame show nudity? Answer yes or no."
        "<|im_end|>\n" + ASSISTANT_PLAIN
    )
    assert result["scores"]["violence"] == pytest.approx(0.9)
    assert result["scores"]["drugs"] == pytest.approx(0.2)
    assert result["asked"] == sorted(result["scores"]) and "transcript" not in result


def test_seq_conversation_generates_until_eos_or_limit_and_keeps_transcripts(tmp_path):
    runtime = FakeRuntime(gen_length=3)
    spec = SPECS["qwen38-27b-mlx-seq"]

    result = converse(runtime, spec, tmp_path / "f.jpg", vlm_questions())

    assert [call[0] for call in runtime.calls].count("begin") == 1
    assert runtime.calls[0][1].endswith(ASSISTANT)  # thinking disabled for Qwen3.8
    assert "Locate all sensitive objects with bounding boxes." in runtime.calls[0][1]
    assert [len(text) for text in result["transcript"]] == [60] * 4
    assert set(result["scores"]) == {"drugs", "nudity", "sex", "violence", "fear/hemophobia"}

    endless = FakeRuntime(gen_length=None)
    result = converse(endless, spec, tmp_path / "f.jpg", vlm_questions())
    # max_tokens pushes per turn, plus one answer push for each question but the last.
    assert [call[0] for call in endless.calls].count("push") == 4 * spec.max_tokens + 4
    assert [len(text) for text in result["transcript"]] == [TRANSCRIPT_LIMIT] * 4


def test_backend_loads_lazily_once_and_frees_on_close(tmp_path, monkeypatch):
    runtimes = []

    def factory(model_dir):
        runtimes.append(FakeRuntime())
        return runtimes[-1]

    backend = QwenMlxBackend(SPECS["qwen3vl-8b-mlx"], runtime_factory=factory)
    monkeypatch.setattr(backend, "check_available", lambda: None)
    monkeypatch.setattr(backend, "model_dir", lambda: tmp_path)

    assert backend.default_concurrency == 1 and not runtimes
    backend.score_frames([tmp_path / "a.jpg"])
    backend.score_frames([tmp_path / "b.jpg", tmp_path / "c.jpg"])
    assert len(runtimes) == 1
    backend.close()
    assert runtimes[0].closed and backend._runtime is None
    meta = backend.meta()
    assert meta["supports"] == ["drugs", "fear/hemophobia", "nudity", "sex", "violence"]


def test_hub_model_is_unavailable_until_downloaded(monkeypatch):
    import huggingface_hub

    def missing(*args, **kwargs):
        raise FileNotFoundError("not cached")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", missing)
    backend = QwenMlxBackend(SPECS["qwen38-27b-mlx"])

    with pytest.raises(BackendUnavailable, match="lmstudio-community/Qwen3.8-27B-MLX-4bit"):
        backend.model_dir()


def test_frame_image_uses_the_shared_image_size_setting(tmp_path, monkeypatch):
    from PIL import Image

    frame = tmp_path / "f.jpg"
    Image.new("RGB", (768, 432), "red").save(frame)
    monkeypatch.delenv("EDITARR_DM_IMAGE_LONG_EDGE", raising=False)
    assert frame_image(frame) == frame
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "512")
    assert frame_image(frame).size == (512, 288)


def test_mlx_ids_are_registered():
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    assert {
        "qwen3vl-8b-mlx",
        "qwen3vl-8b-mlx-seq",
        "qwen38-27b-mlx",
        "qwen38-27b-mlx-seq",
    } <= set(BACKENDS)
    assert isinstance(BACKENDS["qwen3vl-8b-mlx"](), QwenMlxBackend)
