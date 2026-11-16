"""Generate bounded CC0 frame sequences for the MTS credits evaluation corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from .credits_eval import CreditsEvaluationCase, load_credits_evaluation_corpus


def _background(case: CreditsEvaluationCase, frame_index: int) -> np.ndarray:
    """Generate the base frame used by credits fixture rendering."""
    seed = int(hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:8], 16)
    image = np.zeros((240, 426, 3), dtype=np.uint8)
    if "moving_imagery" in case.tags:
        image[:, :, 0] = (seed + frame_index * 31) % 96
        image[:, :, 1] = (seed // 7 + frame_index * 47) % 112
        cv2.circle(
            image,
            (70 + frame_index * 37 % 300, 120),
            55,
            (90, 145, 180),
            -1,
        )
    return image


def generate_case_tail(case: CreditsEvaluationCase, output_dir: Path) -> Path:
    """Render one deterministic sampled media tail and return its manifest path."""
    case_dir = output_dir / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    rendered_frames = []
    for index, observation in enumerate(case.observations):
        image = _background(case, index)
        if observation.black_frame >= 0.85:
            image.fill(0)
        elif observation.text_density >= 0.5:
            offset = -((index * 9) % 24) if observation.credit_motion >= 0.5 else 0
            labels = (
                "CAST AND CREW",
                "ALEX EXAMPLE",
                "SAM SAMPLE",
                "EDITARR STUDIOS",
                "THE END",
            )
            for line_index, label in enumerate(labels):
                cv2.putText(
                    image,
                    label,
                    (62, 42 + line_index * 38 + offset),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (245, 245, 245),
                    2,
                    cv2.LINE_AA,
                )
        else:
            cv2.rectangle(image, (35 + index * 7, 70), (170 + index * 7, 180), (85, 130, 95), -1)
        frame_name = f"frame-{index:03d}.png"
        if not cv2.imwrite(str(case_dir / frame_name), image):
            raise OSError(f"Could not generate credits fixture frame: {frame_name}")
        rendered_frames.append(
            {"path": frame_name, "timestamp_seconds": observation.timestamp_seconds}
        )
    manifest = {
        "version": "mts-credits-generated-tail-v1",
        "license": "CC0-1.0",
        "case_id": case.case_id,
        "duration_seconds": case.duration_seconds,
        "expected_boundary_seconds": case.expected_boundary_seconds,
        "tolerance_seconds": case.tolerance_seconds,
        "tags": list(case.tags),
        "frames": rendered_frames,
    }
    manifest_path = case_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def generate_credits_fixture_corpus(corpus_path: Path, output_dir: Path) -> list[Path]:
    """Render all legal synthetic tails described by a validated corpus."""
    corpus = load_credits_evaluation_corpus(corpus_path)
    return [generate_case_tail(case, output_dir) for case in corpus.cases]
