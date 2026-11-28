"""Qwen video detectors with mocked models (no weights)."""

import math
from types import SimpleNamespace

import pytest
from PIL import Image

from scanner.models import McfCategoryFamily
from scanner.pipeline import qwen_detector, visual
from scanner.settings.capabilities import QWEN38_27B_MODEL, QWEN38_27B_VIDEO_MODEL

F = McfCategoryFamily
QUESTIONS = {
    F.DRUGS: "Does this video frame show drugs, alcohol, smoking, or gambling?",
    F.NUDITY: "Does this video frame show nudity?",
    F.SEX: "Does this video frame show sexual content?",
    F.VIOLENCE: "Does this video frame show violence?",
}


def test_yes_probability_normalizes_yes_and_no_candidates():
    top = [("Yes", math.log(0.3)), (" no", math.log(0.1)), ("NO.", math.log(0.1)), ("x", 0.0)]

    assert qwen_detector.yes_probability(top) == pytest.approx(0.6)
    assert qwen_detector.yes_probability([("maybe", 0.0)]) == 0.0


class _Conversation:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def _step(self):
        yes = next(self.answers)
        return qwen_detector.Step(
            token_id=1 if yes >= 0.5 else 2,
            top=[("Yes", math.log(yes)), ("No", math.log(1 - yes))],
        )

    def begin(self, prompt, image):
        self.calls.append(("begin", prompt, image))
        return self._step()

    def extend(self, text):
        self.calls.append(("extend", text))
        return self._step()

    def push(self, token_id):
        self.calls.append(("push", token_id))

    def end(self):
        self.calls.append(("end",))


def test_qwen_chains_the_evaluated_questions_in_one_conversation(tmp_path):
    conversation = _Conversation([0.1, 0.8, 0.2, 0.6])
    frame = tmp_path / "frame.jpg"

    scores = qwen_detector.converse(conversation, frame)

    assert scores == pytest.approx({F.DRUGS: 0.1, F.NUDITY: 0.8, F.SEX: 0.2, F.VIOLENCE: 0.6})
    assert conversation.calls == [
        (
            "begin",
            "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>"
            f"{QUESTIONS[F.DRUGS]} Answer yes or no.<|im_end|>\n<|im_start|>assistant\n",
            frame,
        ),
        ("push", 2),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.NUDITY]} Answer yes or no.")),
        ("push", 1),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.SEX]} Answer yes or no.")),
        ("push", 2),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.VIOLENCE]} Answer yes or no.")),
        ("end",),
    ]
    assert qwen_detector.next_turn("Q") == (
        "<|im_end|>\n<|im_start|>user\nQ<|im_end|>\n<|im_start|>assistant\n"
    )


def test_qwen38_scores_coarse_and_fine_frames_without_thinking(monkeypatch, tmp_path):
    conversations = []

    def conversation(video_model):
        assert video_model == QWEN38_27B_VIDEO_MODEL
        result = _Conversation([0.1, 0.8, 0.2, 0.6])
        conversations.append(result)
        return result

    monkeypatch.setattr(qwen_detector, "_conversation", conversation)
    frame = tmp_path / "frame.jpg"
    coarse = visual.scan_frame(frame, 1.0, video_model=QWEN38_27B_VIDEO_MODEL)
    fine = visual.scan_frames(
        [(frame, 1.0)], "scan", video_model=QWEN38_27B_VIDEO_MODEL, frame_interval_seconds=0.5
    )

    assert [(item.category, item.label) for item in coarse] == [
        (F.NUDITY, "qwen3_8_nudity"), (F.VIOLENCE, "qwen3_8_violence")
    ]
    assert [(item.start_seconds, item.end_seconds, item.label) for item in fine] == [
        (1.0, 1.5, item.label) for item in coarse
    ]
    assert len(conversations) == 2
    non_thinking_prefix = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    for result in conversations:
        turns = [call[1] for call in result.calls if call[0] in {"begin", "extend"}]
        assert len(turns) == 4
        assert all(turn.endswith(non_thinking_prefix) for turn in turns)
        assert [turn.count("<|image_pad|>") for turn in turns] == [1, 0, 0, 0]
        assert result.calls[-1] == ("end",)


def test_qwen_conversation_accepts_a_frame_path_or_a_decoded_image(monkeypatch, tmp_path):
    seen = []
    modules = {
        "mlx_vlm.utils": SimpleNamespace(
            prepare_inputs=lambda _processor, images, **_kwargs: seen.append(images[0])
            or {"input_ids": "ids"}
        ),
        "mlx_vlm.models.cache": SimpleNamespace(make_prompt_cache=lambda _language: []),
    }
    monkeypatch.setattr(
        qwen_detector, "require_dependency", lambda name, *_args: modules.get(name)
    )
    embedded = SimpleNamespace(inputs_embeds="embeds", to_dict=lambda: {})
    model = SimpleNamespace(
        language_model=SimpleNamespace(),
        config=SimpleNamespace(),
        get_input_embeddings=lambda *_args, **_kwargs: embedded,
    )
    conversation = qwen_detector.QwenConversation(model, SimpleNamespace(tokenizer=None))
    monkeypatch.setattr(conversation, "_forward", lambda *_args: "step")
    image, frame = Image.new("RGB", (4, 4)), tmp_path / "frame.jpg"

    assert conversation.begin("prompt", frame) == "step"
    assert conversation.begin("prompt", image) == "step"
    assert seen == [str(frame), image]


def test_qwen_conversation_keeps_only_rope_deltas_for_cached_continuations(monkeypatch, tmp_path):
    cache = [object(), object()]
    language = SimpleNamespace(_rope_deltas="stale", _position_ids="stale")
    modules = {
        "mlx.core": SimpleNamespace(array=lambda tokens: tokens),
        "mlx_vlm.utils": SimpleNamespace(
            prepare_inputs=lambda *_args, **_kwargs: {"input_ids": "ids", "image_grid_thw": "grid"}
        ),
        "mlx_vlm.models.cache": SimpleNamespace(make_prompt_cache=lambda _language: cache),
    }
    monkeypatch.setattr(qwen_detector, "require_dependency", lambda name, *_args: modules[name])
    embedded = SimpleNamespace(
        inputs_embeds="embeds", to_dict=lambda: {"position_ids": "positions", "rope_deltas": "rope"}
    )
    model = SimpleNamespace(
        language_model=language,
        config=SimpleNamespace(),
        get_input_embeddings=lambda *_args, **_kwargs: embedded,
    )
    tokenizer = SimpleNamespace(encode=lambda *_args, **_kwargs: [1, 2])
    conversation = qwen_detector.QwenConversation(model, SimpleNamespace(tokenizer=tokenizer))
    forwards = []

    def forward(*args):
        assert conversation._cache is cache
        forwards.append((args, conversation._kwargs))

    monkeypatch.setattr(conversation, "_forward", forward)
    conversation.begin("prompt", tmp_path / "frame.jpg")
    conversation.extend("continuation")
    conversation.end()

    assert forwards == [
        (
            ("ids", "embeds"),
            {"image_grid_thw": "grid", "position_ids": "positions", "rope_deltas": "rope"},
        ),
        (([[1, 2]],), {"rope_deltas": "rope"}),
    ]
    assert conversation._cache is None
    assert language._rope_deltas is language._position_ids is None


@pytest.mark.parametrize(
    ("video_model", "artifact", "revision"),
    [
        ("qwen3-vl-8b", "qwen3-vl-8b-instruct-4bit", "defcdea7cc7a4b0858fea563cbbce171d328e457"),
        ("qwen3-vl-4b", "qwen3-vl-4b-instruct-4bit", "2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b"),
        (QWEN38_27B_VIDEO_MODEL, QWEN38_27B_MODEL, "b935172e4bb5dcd0543774a34748fb92422e3027"),
    ],
)
def test_qwen_detector_shares_the_description_model_loader(
    monkeypatch, video_model, artifact, revision
):
    loaded = []
    model, processor = object(), object()
    monkeypatch.setattr(qwen_detector, "require_available", lambda _backend: None)
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary._load_model",
        lambda artifact_id: loaded.append(artifact_id) or (model, processor, object()),
    )
    monkeypatch.setattr(qwen_detector, "QwenConversation", lambda *args: args)

    assert qwen_detector._conversation(video_model) == (model, processor)
    assert loaded == [artifact]
    assert qwen_detector.detector(video_model).model_revision == revision
