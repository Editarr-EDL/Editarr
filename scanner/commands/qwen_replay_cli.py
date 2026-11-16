"""Command-line replay tool for testing Qwen prompts against saved scan frames."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner.pipeline.visual import replay_qwen_summaries


def _read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def main() -> None:
    """Parse replay arguments and rerun Qwen summaries from a saved bundle."""
    parser = argparse.ArgumentParser(
        description="Replay saved Qwen frame summaries without rerunning a video scan"
    )
    parser.add_argument("replay_dir", type=Path, help="Directory containing manifest.json")
    parser.add_argument("--prompt-file", type=Path, help="File containing a replacement prompt")
    args = parser.parse_args()

    findings = replay_qwen_summaries(
        args.replay_dir,
        prompt=_read_prompt(args.prompt_file),
    )
    print(json.dumps([finding.model_dump(mode="json") for finding in findings], indent=2))


if __name__ == "__main__":
    main()
