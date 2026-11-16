"""Command-line entry point for deterministic credits-detector evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner.credits.credits_eval import evaluate_credits_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate closing-credits boundary detection")
    parser.add_argument("corpus", type=Path, help="Path to the credits corpus JSON")
    parser.add_argument("--output", type=Path, help="Write JSON results to this file")
    args = parser.parse_args()

    output = json.dumps(evaluate_credits_corpus(args.corpus), indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
