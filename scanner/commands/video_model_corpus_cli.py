"""Command-line entry point for adding reviewed video ranges to an eval corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner.evaluation.video_model_corpus import add_labeled_range


def main() -> None:
    """Build or inspect the video model evaluation corpus from CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Extract a labeled video range into a visual-model evaluation corpus"
    )
    parser.add_argument("video", type=Path, help="Source video file")
    parser.add_argument("corpus", type=Path, help="Directory containing the corpus manifest")
    parser.add_argument("--start", required=True, type=float, help="Range start in seconds")
    parser.add_argument("--end", required=True, type=float, help="Range end in seconds")
    parser.add_argument(
        "--categories",
        default="",
        help="Comma-separated expected categories; omit for clean frames",
    )
    parser.add_argument(
        "--interval",
        default=1.0,
        type=float,
        help="Seconds between extracted frames (default: 1.0)",
    )
    args = parser.parse_args()

    result = add_labeled_range(
        args.video,
        args.corpus,
        start_seconds=args.start,
        end_seconds=args.end,
        categories=args.categories,
        interval_seconds=args.interval,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
