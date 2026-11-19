"""Shieldstral MLX engine logic exercised with NumPy stand-ins for MLX."""

from types import SimpleNamespace

import numpy as np
import pytest

from scanner.pipeline.shieldstral_inference import (
    ShieldstralEngine,
    answer_token_ids,
    common_prefix_length,
    supports_prefix_cache,
    yes_probability,
)
from scanner.pipeline.shieldstral_policies import ALL_QUERIES, INSTRUCT, SYSTEM_PROMPT

IMAGE_TOKEN = 10
ANSWERS = {"yes": 21, " yes": 22, "Yes": 23, " Yes": 24, "no": 25, " no": 26, "No": 27, " No": 28}


class _Tokenizer:
    def __init__(self, vocab):
        self.vocab = vocab

    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        value = self.vocab[text]
        return value if isinstance(value, list) else [value]


def test_answer_tokens_include_every_case_and_space_variant():
    assert answer_token_ids(_Tokenizer(ANSWERS)) == ((21, 22, 23, 24), (25, 26, 27, 28))


@pytest.mark.parametrize("broken", [{"Yes": [1, 2]}, {" No": 21}])
def test_ambiguous_answer_tokens_are_rejected(broken):
    with pytest.raises(RuntimeError):
        answer_token_ids(_Tokenizer(ANSWERS | broken))


def test_yes_probability_compares_best_yes_and_no_logits():
    logits = np.zeros(10)
    logits[[1, 2, 3, 4]] = [0.5, 2.0, 1.0, -3.0]
    assert yes_probability(logits, (1, 2), (3, 4)) == pytest.approx(1 / (1 + np.exp(-1.0)))
    logits[3] = 5000.0
    assert yes_probability(logits, (1, 2), (3, 4)) == 0.0


@pytest.mark.parametrize(
    ("sequences", "expected"),
    [
        ([[1, 2, 3, 7, IMAGE_TOKEN, 4], [1, 2, 3, 8, IMAGE_TOKEN, 4]], 3),
        ([[1, 2, IMAGE_TOKEN, 4], [1, 2, IMAGE_TOKEN, 4]], 2),
        ([[1, 2, 3], [1, 2, 3]], 2),
        ([[5, 2], [6, 2]], 0),
        ([], 0),
    ],
)
def test_common_prefix_stops_at_divergence_or_the_image(sequences, expected):
    assert common_prefix_length(sequences, IMAGE_TOKEN) == expected


class _Embedding(dict):
    """Tied, unquantized output rows: yes ids read hidden[0], no ids read hidden[1]."""

    def __init__(self):
        weight = np.zeros((32, 2), dtype=np.float32)
        weight[21:25, 0] = 1.0
        weight[25:29, 1] = 1.0
        super().__init__(weight=weight)

    def __call__(self, ids):
        return np.zeros((*np.asarray(ids).shape, 2), dtype=np.float32)


class _KVCache:
    def __init__(self):
        self.offset = 0
        self.state = ()

    def trim(self, count):
        self.offset -= count


class _Decoder:
    swa_idx = None

    def __init__(self):
        self.embed_tokens = _Embedding()
        self.layers = [SimpleNamespace(use_sliding=False)] * 2
        self.calls = []

    def __call__(self, ids, cache=None, inputs_embeds=None):
        ids = np.asarray(ids)
        self.calls.append((ids[0].tolist(), cache[0].offset if cache else None))
        if cache:
            for layer_cache in cache:
                layer_cache.offset += ids.shape[1]
        hidden = np.zeros((1, ids.shape[1], 2), dtype=np.float32)
        query_tokens = [token for token in ids[0].tolist() if token >= 100]
        hidden[0, -1, 0] = float(query_tokens[0] - 100) if query_tokens else 0.0
        return hidden


class _Model:
    vision_feature_layer = -1

    def __init__(self):
        self.config = SimpleNamespace(image_token_index=IMAGE_TOKEN)
        self.language_model = SimpleNamespace(
            model=_Decoder(), config=SimpleNamespace(tie_word_embeddings=True)
        )
        self.vision_calls = 0
        self.features = np.ones((2, 2), dtype=np.float32)

    def vision_tower(self, values, *, output_hidden_states, image_sizes):
        self.vision_calls += 1
        return None, [np.zeros((1, 2, 2))]

    def multi_modal_projector(self, selected, image_sizes):
        return self.features

    def merge_input_ids_with_image_features(self, token, features, embeddings, ids):
        assert token == IMAGE_TOKEN and features is self.features
        return embeddings


class _Processor:
    """Renders prompts as text and tokenizes them as [BOS, system, instruct, query, image]."""

    def __init__(self):
        self.tokenizer = _Tokenizer(ANSWERS)
        self.text_calls = 0

    def apply_chat_template(self, messages, *, add_generation_prompt, tokenize, chat_template):
        assert (add_generation_prompt, tokenize, chat_template) == (True, False, "TEMPLATE")
        system, user = messages
        parts = [system["content"][0]["text"], "|"]
        parts += ["<IMG>" if item["type"] == "image" else item["text"] for item in user["content"]]
        return "".join(parts)

    def __call__(self, *, images, text=None, return_tensors):
        pixels = {"pixel_values": np.ones((1, 3, 4, 4)), "image_sizes": np.array([[4, 4]])}
        if text is None:
            return pixels
        self.text_calls += 1
        query = next(q for q in ALL_QUERIES if f"<Query>: {q}\n" in text[0])
        ids = [1, 2, 3, 100 + ALL_QUERIES.index(query), IMAGE_TOKEN, IMAGE_TOKEN, 4]
        return {"input_ids": np.array([ids]), "attention_mask": np.ones((1, len(ids))), **pixels}


def _engine(model=None, kv_cache=_KVCache):
    fake_mx = SimpleNamespace(
        array=np.asarray,
        eval=lambda *_: None,
        float32=np.float32,
        bfloat16=np.float32,
        max=lambda values, axis: values.max(axis=axis),
        sigmoid=lambda values: 1 / (1 + np.exp(-values)),
    )
    return ShieldstralEngine(
        model or _Model(), _Processor(), "TEMPLATE", mx=fake_mx, numpy=np, kv_cache=kv_cache
    )


def _image(size=(512, 288)):
    return SimpleNamespace(size=size)


def test_prompts_change_only_the_query():
    engine = _engine()
    for query in ALL_QUERIES:
        assert engine.prompt(query) == (
            f"{SYSTEM_PROMPT}|<Instruct>: {INSTRUCT}\n\n<Query>: {query}\n\n<Document>: <IMG>\n\n"
        )


def test_input_ids_are_cached_per_query_and_image_size():
    engine = _engine()
    engine.input_ids(ALL_QUERIES[0], _image())
    engine.input_ids(ALL_QUERIES[0], _image())
    engine.input_ids(ALL_QUERIES[0], _image((576, 288)))
    engine.input_ids(ALL_QUERIES[1], _image())

    assert engine.processor.text_calls == 3


def test_fast_path_shares_the_prefix_and_encodes_each_frame_once():
    model = _Model()
    engine = _engine(model)
    decoder = model.language_model.model
    assert engine.fast

    frame = engine.frame(_image())
    scores = [frame.score(query) for query in ALL_QUERIES[:3]]
    assert frame.score(ALL_QUERIES[0]) == scores[0]
    engine.frame(_image()).score(ALL_QUERIES[1])

    assert model.vision_calls == 2
    prefix_call, *policy_calls = decoder.calls
    assert prefix_call == ([1, 2, 3], 0)
    assert policy_calls == [
        ([100, IMAGE_TOKEN, IMAGE_TOKEN, 4], 3),
        ([101, IMAGE_TOKEN, IMAGE_TOKEN, 4], 3),
        ([102, IMAGE_TOKEN, IMAGE_TOKEN, 4], 3),
        ([101, IMAGE_TOKEN, IMAGE_TOKEN, 4], 3),
    ]
    assert engine._cache[0].offset == 3
    assert scores == pytest.approx([0.5, 1 / (1 + np.exp(-1)), 1 / (1 + np.exp(-2))])


@pytest.mark.parametrize(
    "breaks",
    [
        lambda model: setattr(
            model.language_model.model, "layers", [SimpleNamespace(use_sliding=True)]
        ),
        lambda model: setattr(model, "merge_input_ids_with_image_features", None),
        lambda model: setattr(model.config, "image_token_index", None),
    ],
)
def test_unexpected_structure_disables_the_fast_path(breaks):
    model = _Model()
    assert supports_prefix_cache(model)
    breaks(model)
    assert not supports_prefix_cache(model)
    assert not _engine(model).fast


class _PlainModel(_Model):
    def __call__(self, input_ids, pixel_values, mask, **kwargs):
        logits = np.zeros((1, 2, 32), dtype=np.float32)
        logits[0, -1, 23] = 2.0  # "Yes"
        logits[0, -1, 26] = 1.0  # " no"
        return SimpleNamespace(logits=logits)


def test_plain_fallback_reads_every_answer_variant():
    engine = _engine(_PlainModel(), kv_cache=None)
    assert not engine.fast
    assert engine.frame(_image()).score(ALL_QUERIES[0]) == pytest.approx(1 / (1 + np.exp(-1.0)))
