"""Test working-tree Qwen language prompts from a local audio file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scanner.evaluation.qwen_language_replay import compare_language_replays, replay_qwen_language
from scanner.runtime.durability import atomic_write_bytes
from scanner.settings.capabilities import (
    DEFAULT_AUDIO_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS,
)
from scanner.settings.config import settings
from scanner.settings.model_scan_settings import load_model_scan_settings


def _run(args: argparse.Namespace) -> int:
    if args.audio.resolve() == args.output.resolve():
        raise ValueError("The output path must differ from the input audio file")
    audio_padding = args.audio_padding_seconds
    if audio_padding is None:
        audio_padding = load_model_scan_settings(
            settings.data_dir, default_audio_padding=settings.profanity_pad_seconds
        ).audio_padding_seconds

    previous = None

    def show_progress(fraction: float, message: str) -> None:
        nonlocal previous
        step = (int(fraction * 10), message)
        if step != previous:
            print(f"{fraction:4.0%} {message}", file=sys.stderr)
            previous = step

    report = replay_qwen_language(
        args.audio,
        cache_dir=args.cache_dir,
        audio_model=args.audio_model,
        qwen_model=args.qwen_model,
        audio_padding_seconds=audio_padding,
        deny_words=args.deny_word,
        progress_callback=show_progress,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        args.output,
        (report.model_dump_json(indent=2) + "\n").encode("utf-8"),
        prefix=".qwen-language-replay-",
    )
    if report.status != "complete":
        print(
            f"Qwen did not complete ({report.status}). Report saved to {args.output}.",
            file=sys.stderr,
        )
        if report.status == "no_inputs":
            print("Transcription returned no timed speech for Qwen to test.", file=sys.stderr)
        if report.status == "unsupported":
            print("Qwen needs Apple silicon with a working MLX runtime and model.", file=sys.stderr)
        return 1
    print(f"Saved {len(report.findings)} findings to {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run a language prompt replay or compare two saved replay reports."""
    parser = argparse.ArgumentParser(description="Replay and compare local Qwen language prompts")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run Qwen from an audio file and save the findings")
    run.add_argument("audio", type=Path, help="Extracted audio file")
    run.add_argument("--output", type=Path, required=True, help="Write the JSON replay report")
    run.add_argument(
        "--cache-dir",
        type=Path,
        default=settings.cache_dir / "qwen-language-replays",
        help="Reusable transcripts (default: qwen-language-replays in Editarr's cache)",
    )
    run.add_argument("--audio-model", choices=SUPPORTED_AUDIO_MODELS, default=DEFAULT_AUDIO_MODEL)
    run.add_argument(
        "--qwen-model", choices=SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS, default=DEFAULT_QWEN_MODEL
    )
    run.add_argument(
        "--audio-padding-seconds", type=float, help="Override Editarr's configured audio padding"
    )
    run.add_argument(
        "--deny-word", action="append", help="Seed Qwen's word classification (repeatable)"
    )

    compare = commands.add_parser("compare", help="Compare timed findings from two reports")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        print(compare_language_replays(args.baseline, args.candidate))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
