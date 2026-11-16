"""Command-line entry point for MCF subcategorization corpus evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner.evaluation.subcategorization_eval import (
    evaluate_detector_mapping_corpus,
    evaluate_subcategorization_corpus,
)


def main() -> None:
    """Run the subcategorization evaluation for the selected fixture set."""
    parser = argparse.ArgumentParser(
        description="Measure constrained MCF subcategorization responses"
    )
    parser.add_argument("corpus", type=Path, help="Path to the evaluation corpus JSON")
    parser.add_argument(
        "--detector-labels",
        action="store_true",
        help="Evaluate deterministic producer-label mappings instead of Qwen responses",
    )
    parser.add_argument("--output", type=Path, help="Write JSON results to this file")
    args = parser.parse_args()

    evaluator = (
        evaluate_detector_mapping_corpus
        if args.detector_labels
        else evaluate_subcategorization_corpus
    )
    output = json.dumps(evaluator(args.corpus), indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
