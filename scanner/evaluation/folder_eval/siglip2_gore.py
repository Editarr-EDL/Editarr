"""Zero-shot blood/gore scoring with the repo's SigLIP2 checkpoint (image + text towers)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

ARTIFACT_ID = "siglip2-base-patch16-256"
KEY = "fear/hemophobia"
POSITIVE_PROMPTS = (
    "a photo with blood",
    "a gory, bloody wound",
    "graphic gore",
    "a person covered in blood",
    "blood splatter",
)
# Scored for inspection only (in `labels`); they do not affect the key's score.
NEUTRAL_PROMPTS = ("a photo", "a scene from a movie")
# Calibrated on the decision sample's clip items (not the films); see meta notes.
DEFAULT_THRESHOLD = 0.004
TEXT_MAX_LENGTH = 64  # SigLIP text towers were trained on max-length padded prompts.


class Siglip2GoreBackend:
    """`fear/hemophobia` = max sigmoid(image·text) over blood/gore prompts."""

    model_id = "siglip2-gore"

    def __init__(self) -> None:
        from scanner.model_acquisition import get_acquisition_service
        from scanner.pipeline import siglip2_moderation
        from scanner.settings.config import settings

        self._model_dir = get_acquisition_service().acquire(ARTIFACT_ID)
        processor, model, execution, torch = siglip2_moderation._load_encoder()
        if not hasattr(model, "get_text_features"):
            raise RuntimeError(f"{ARTIFACT_ID} checkpoint has no text tower")
        self._processor, self._model, self._execution, self._torch = (
            processor, model, execution, torch,
        )
        self._batch_size = max(1, settings.visual_batch_size)
        self._prompts = POSITIVE_PROMPTS + NEUTRAL_PROMPTS
        self._text = self._text_embeddings(self._prompts)
        self._scale = float(model.logit_scale.exp().item())
        self._bias = float(model.logit_bias.item())

    def _text_embeddings(self, prompts: tuple[str, ...]):
        torch = self._torch
        inputs = self._processor(
            text=list(prompts), padding="max_length", max_length=TEXT_MAX_LENGTH,
            truncation=True, return_tensors="pt",
        )

        def embed(device):
            with torch.inference_mode():
                features = self._model.get_text_features(input_ids=inputs["input_ids"].to(device))
                features = getattr(features, "pooler_output", features)
                return torch.nn.functional.normalize(features, dim=-1).cpu().float()

        return self._execution.run(embed)

    def _image_embeddings(self, paths: list[Path]):
        from PIL import Image

        from scanner.pipeline.siglip2_moderation import _image_embedding

        torch = self._torch
        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        inputs = self._processor(images=images, return_tensors="pt")

        def embed(device):
            moved = {
                key: value.to(device) if torch.is_tensor(value) else value
                for key, value in inputs.items()
            }
            with torch.inference_mode():
                features = _image_embedding(self._model.get_image_features(**moved))
                return torch.nn.functional.normalize(features, dim=-1).cpu().float()

        return self._execution.run(embed)

    def probabilities(self, paths: list[Path]) -> list[dict[str, float]]:
        """Per frame: prompt → sigmoid(cosine × scale + bias)."""
        results = []
        for start in range(0, len(paths), self._batch_size):
            logits = self._image_embeddings(paths[start : start + self._batch_size]) @ self._text.T
            for row in logits.tolist():
                results.append({
                    prompt: 1.0 / (1.0 + math.exp(-(value * self._scale + self._bias)))
                    for prompt, value in zip(self._prompts, row, strict=True)
                })
        return results

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        outputs = []
        for probabilities in self.probabilities(paths):
            score = max(probabilities[prompt] for prompt in POSITIVE_PROMPTS)
            labels = [f"{prompt} {value:.4f}" for prompt, value in probabilities.items()]
            outputs.append({"scores": {KEY: score}, "labels": labels})
        return outputs

    def meta(self) -> dict[str, Any]:
        from scanner.settings.capabilities import capability_for

        revision = Path(self._model_dir).name
        return {
            "model_id": self.model_id,
            "display_name": "SigLIP2 zero-shot blood/gore",
            "supports": [KEY],
            "thresholds": {KEY: DEFAULT_THRESHOLD, "*": DEFAULT_THRESHOLD},
            "runtime": f"torch ({capability_for('siglip2_moderation_nsfw').device})",
            "artifact": f"google/{ARTIFACT_ID} (image + text towers)",
            "revision": revision,
            "prompts": {"positive": list(POSITIVE_PROMPTS), "neutral": list(NEUTRAL_PROMPTS)},
            "notes": (
                "fear/hemophobia = max over positive prompts of SigLIP's sigmoid image-text "
                "probability (labels keep every prompt's probability). Threshold "
                f"{DEFAULT_THRESHOLD}: F1-optimal (0.0038, F1 0.37, AP 0.26) on the 920 "
                "decision-sample clip frames (25 positive, Violent Scene Dataset blood/gore), "
                "rounded; films were not used. Normalising against the neutral prompts "
                "lowered AP (0.18), so they are recorded only."
            ),
        }

    def close(self) -> None:
        from .backends import release_local_models

        self._processor = self._model = self._execution = self._text = None
        release_local_models()
