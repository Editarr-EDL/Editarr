"""MLX scoring engine that turns Shieldstral next-token logits into P(yes).

The fast path mirrors the evaluated reference implementation: the image is encoded
once per frame, the system + instruct prefix shared by every policy prompt is kept in
a per-process KV cache, each policy pre-fills only its remaining tokens, and the
yes/no logits are computed in float32 from the final hidden state against the
dequantized answer rows of the output projection. Models whose structure differs use
the plain full forward pass instead.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from threading import Lock

from .shieldstral_policies import ALL_QUERIES, policy_messages

YES_VARIANTS = ("yes", " yes", "Yes", " Yes")
NO_VARIANTS = ("no", " no", "No", " No")


def answer_token_ids(tokenizer) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Resolve single-token yes/no answer ids (all case and space variants)."""

    def resolve(variants: tuple[str, ...]) -> tuple[int, ...]:
        ids = []
        for variant in variants:
            encoded = list(tokenizer.encode(variant, add_special_tokens=False))
            if len(encoded) != 1:
                raise RuntimeError(f"Shieldstral answer {variant!r} is not a single token")
            ids.append(int(encoded[0]))
        return tuple(dict.fromkeys(ids))

    yes_ids, no_ids = resolve(YES_VARIANTS), resolve(NO_VARIANTS)
    if set(yes_ids) & set(no_ids):
        raise RuntimeError("Shieldstral yes and no answers share a token")
    return yes_ids, no_ids


def yes_probability(logits: Sequence[float], yes_ids, no_ids) -> float:
    """Return P(yes) from the best yes and no logits of the next-token distribution."""
    margin = max(float(logits[i]) for i in no_ids) - max(float(logits[i]) for i in yes_ids)
    if margin > 0:
        odds = math.exp(-margin)
        return odds / (1.0 + odds)
    return 1.0 / (1.0 + math.exp(margin))


def common_prefix_length(sequences: Sequence[Sequence[int]], stop_token: int) -> int:
    """Return how many leading tokens all prompts share, ending before any image token.

    The cached prefix must contain text only, so it never extends past ``stop_token``,
    and at least one token is always left for the per-policy suffix.
    """
    if not sequences:
        return 0
    limit = min(len(sequence) for sequence in sequences) - 1
    for index in range(max(limit, 0)):
        tokens = {sequence[index] for sequence in sequences}
        if len(tokens) > 1 or stop_token in tokens:
            return index
    return max(limit, 0)


def supports_prefix_cache(model) -> bool:
    """Check for the Mistral3/Ministral3 structure the fast path relies on."""
    language_model = getattr(model, "language_model", None)
    decoder = getattr(language_model, "model", None)
    layers = getattr(decoder, "layers", None)
    return bool(
        layers
        and callable(getattr(model, "merge_input_ids_with_image_features", None))
        and isinstance(getattr(getattr(model, "config", None), "image_token_index", None), int)
        and isinstance(getattr(model, "vision_feature_layer", None), int)
        and hasattr(model, "vision_tower")
        and hasattr(model, "multi_modal_projector")
        and hasattr(decoder, "embed_tokens")
        and getattr(decoder, "swa_idx", None) is None
        and not any(getattr(layer, "use_sliding", False) for layer in layers)
    )


class ShieldstralEngine:
    """Score policy queries against frames with one loaded Shieldstral model.

    ``kv_cache`` is the per-layer cache class (``mlx_vlm.models.cache.KVCache``); the
    engine is not reentrant, so scoring is serialized with a lock.
    """

    def __init__(
        self,
        model,
        processor,
        template: str,
        *,
        mx,
        numpy,
        kv_cache: Callable[[], object] | None,
        queries: tuple[str, ...] = ALL_QUERIES,
    ):
        self.model, self.processor, self.template = model, processor, template
        self.mx, self.numpy = mx, numpy
        self.yes_ids, self.no_ids = answer_token_ids(processor.tokenizer)
        self.queries = queries
        self.lock = Lock()
        self._ids: dict[tuple[str, tuple[int, int]], object] = {}
        self._kv_cache = kv_cache
        self._cache: list | None = None
        self._prefix: list[int] | None = None
        self._shares_prefix: dict[tuple[str, tuple[int, int]], bool] = {}
        self._answer_rows = None
        if kv_cache is not None and supports_prefix_cache(model):
            self._answer_rows = self._load_answer_rows()

    @property
    def fast(self) -> bool:
        return self._answer_rows is not None

    def prompt(self, query: str) -> str:
        # The processor does not adopt the repository chat template, so pass it explicitly.
        return self.processor.apply_chat_template(
            policy_messages(query),
            add_generation_prompt=True,
            tokenize=False,
            chat_template=self.template,
        )

    def input_ids(self, query: str, image):
        """Return the expanded prompt ids, cached per (query, image size)."""
        key = (query, tuple(image.size))
        if key not in self._ids:
            inputs = self.processor(text=[self.prompt(query)], images=[image], return_tensors="np")
            self._ids[key] = self.mx.array(inputs["input_ids"])
        return self._ids[key]

    def frame(self, image) -> FrameScorer:
        return FrameScorer(self, image)

    def _load_answer_rows(self):
        """Dequantize the output-projection rows of the answer tokens once, in float32."""
        mx = self.mx
        language_model = self.model.language_model
        tied = getattr(getattr(language_model, "config", None), "tie_word_embeddings", True)
        head = language_model.model.embed_tokens if tied else getattr(language_model, "lm_head")
        rows = mx.array(self.yes_ids + self.no_ids)
        if "scales" in head:
            biases = head.get("biases")
            weights = mx.dequantize(
                head["weight"][rows],
                scales=head["scales"][rows],
                biases=biases[rows] if biases is not None else None,
                group_size=head.group_size,
                bits=head.bits,
                mode=getattr(head, "mode", "affine"),
            )
        else:
            weights = head["weight"][rows]
        weights = weights.astype(mx.float32)
        mx.eval(weights)
        return weights

    def _ensure_prefix(self, image) -> None:
        """Pre-fill the KV cache with the text prefix shared by every policy prompt."""
        if self._prefix is not None:
            return
        sequences = [self.input_ids(query, image)[0].tolist() for query in self.queries]
        length = common_prefix_length(sequences, self.model.config.image_token_index)
        decoder = self.model.language_model.model
        self._cache = [self._kv_cache() for _ in decoder.layers]
        if length:
            prefix_ids = self.mx.array([sequences[0][:length]])
            embeddings = decoder.embed_tokens(prefix_ids)
            hidden = decoder(prefix_ids, cache=self._cache, inputs_embeds=embeddings)
            self.mx.eval(hidden, [layer_cache.state for layer_cache in self._cache])
        self._prefix = sequences[0][:length]

    def _trim_to_prefix(self) -> None:
        extra = self._cache[0].offset - len(self._prefix)
        if extra > 0:
            for layer_cache in self._cache:
                layer_cache.trim(extra)

    def score_fast(self, query: str, image, features) -> float:
        mx, model = self.mx, self.model
        decoder = model.language_model.model
        ids = self.input_ids(query, image)
        self._ensure_prefix(image)
        length = len(self._prefix)
        key = (query, tuple(image.size))
        if key not in self._shares_prefix:
            self._shares_prefix[key] = bool(length) and ids[0, :length].tolist() == self._prefix
        cache = self._cache
        if self._shares_prefix[key]:
            ids = ids[:, length:]
            self._trim_to_prefix()
        else:
            cache = None
        embeddings = model.merge_input_ids_with_image_features(
            model.config.image_token_index, features, decoder.embed_tokens(ids), ids
        )
        try:
            hidden = decoder(ids, cache=cache, inputs_embeds=embeddings)
            logits = hidden[:, -1].astype(mx.float32) @ self._answer_rows.T
            count = len(self.yes_ids)
            margin = mx.max(logits[:, :count], axis=1) - mx.max(logits[:, count:], axis=1)
            score = mx.sigmoid(margin)
            mx.eval(score)
            return float(score.item())
        finally:
            if cache is not None:
                self._trim_to_prefix()

    def score_plain(self, query: str, image) -> float:
        """Run the reference full forward pass and read the bf16 answer logits."""
        mx = self.mx
        inputs = self.processor(text=[self.prompt(query)], images=[image], return_tensors="np")
        extra = {
            key: mx.array(value)
            for key, value in inputs.items()
            if key not in ("input_ids", "attention_mask", "pixel_values")
        }
        output = self.model(
            mx.array(inputs["input_ids"]),
            mx.array(inputs["pixel_values"]).astype(mx.bfloat16),
            mx.array(inputs["attention_mask"]),
            **extra,
        )
        logits = self.numpy.array(output.logits[0, -1].astype(mx.float32))
        return yes_probability(logits, self.yes_ids, self.no_ids)


class FrameScorer:
    """Score any number of queries for one frame, encoding its image at most once."""

    def __init__(self, engine: ShieldstralEngine, image):
        self.engine, self.image = engine, image
        self._features = None
        self._scores: dict[str, float] = {}

    def _image_features(self):
        """Encode the frame once, mirroring ``Mistral3Model.get_input_embeddings``."""
        if self._features is None:
            mx, model = self.engine.mx, self.engine.model
            inputs = self.engine.processor(images=[self.image], return_tensors="np")
            pixel_values = mx.array(inputs["pixel_values"]).astype(mx.bfloat16)
            if pixel_values.ndim == 3:
                pixel_values = pixel_values[None, ...]
            sizes = mx.array(inputs["image_sizes"])
            *_, hidden_states = model.vision_tower(
                pixel_values.transpose(0, 2, 3, 1), output_hidden_states=True, image_sizes=sizes
            )
            selected = hidden_states[model.vision_feature_layer]
            if selected.ndim == 3 and selected.shape[0] == 1:
                selected = selected.squeeze(0)
            features = model.multi_modal_projector(selected, sizes)
            mx.eval(features)
            self._features = features
        return self._features

    def score(self, query: str) -> float:
        """Return P(yes) for ``query``; repeated queries reuse the first result."""
        if query not in self._scores:
            engine = self.engine
            with engine.lock:
                if engine.fast:
                    value = engine.score_fast(query, self.image, self._image_features())
                else:
                    value = engine.score_plain(query, self.image)
            self._scores[query] = value
        return self._scores[query]
