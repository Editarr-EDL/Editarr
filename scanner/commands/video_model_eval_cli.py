"""Command-line entry point for video-model evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner.evaluation.video_model_eval import evaluate_video_model


def main() -> None:
    """Evaluate a selected video model against the configured dataset."""
    parser = argparse.ArgumentParser(
        description="Measure a video detector against a labeled frame dataset"
    )
    parser.add_argument("dataset", type=Path, help="Path to the evaluation manifest JSON")
    parser.add_argument("--model", required=True, help="Video model identifier to evaluate")
    parser.add_argument(
        "--warmup-frames",
        default=1,
        type=int,
        help="Frames to run before collecting timing (default: 1)",
    )
    parser.add_argument(
        "--runs", default=1, type=int, help="Timed passes over the corpus (default: 1)"
    )
    parser.add_argument("--output", type=Path, help="Write JSON results to this file")
    args = parser.parse_args()

    results = evaluate_video_model(
        args.dataset,
        args.model,
        warmup_frames=args.warmup_frames,
        runs=args.runs,
    )
    output = json.dumps(results, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
