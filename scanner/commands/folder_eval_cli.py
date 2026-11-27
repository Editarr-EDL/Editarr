"""Command-line entry point for the folder-structure evaluation harness.

Run as `editarr-evaluate-folder` or `python -m scanner.evaluation.folder_eval`; see
docs/model-development.md for the data layout and every subcommand.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scanner.evaluation.folder_eval.backends import (
    DECISION_MODEL_IDS,
    LOCAL_BACKENDS,
    BackendUnavailable,
    configure_model_downloads,
)
from scanner.evaluation.folder_eval.film_sources import (
    FilmSourceError,
    parse_film_options,
    resolve_sources,
)
from scanner.evaluation.folder_eval.films import DEFAULT_FILMS_PATH, build_films
from scanner.evaluation.folder_eval.frames import extract_frames
from scanner.evaluation.folder_eval.inventory import write_inventory
from scanner.evaluation.folder_eval.labels import DEFAULT_RULES_PATH, LabelRules
from scanner.evaluation.folder_eval.paths import Layout, default_data_root, normalize_prefix
from scanner.evaluation.folder_eval.runner import run_model
from scanner.evaluation.folder_eval.subsets import (
    DEFAULT_SEED,
    FILMS_QWEN300,
    load_subset,
    write_decision_sample,
    write_film_subset,
)
from scanner.evaluation.folder_eval.summary import summarize
from scanner.evaluation.folder_eval.viewer_install import install_viewer

log = logging.getLogger("folder_eval")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="editarr-evaluate-folder",
        description="Evaluate detectors against labels inferred from evaluation_data/ folders.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="Data folder (default: $EDITARR_EVAL_DATA_ROOT, else evaluation_data/ in the "
        "repository checkout, else in the working directory)",
    )
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("inventory", help="List media, probe durations, apply label rules")
    films = commands.add_parser(
        "films", help="Link films, copy their ground truth, add 10 s windows to the inventory"
    )
    films.add_argument(
        "--config", type=Path, default=DEFAULT_FILMS_PATH, help="Film list (no local paths)"
    )
    films.add_argument(
        "--ground-truth-dir",
        type=Path,
        help="Folder with movies.csv and tag_timestamps.csv (overrides the environment "
        "and the local config)",
    )
    films.add_argument(
        "--film",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="Local file for a film, keyed by IMDb id or 'Title (Year)'; repeatable",
    )
    films.add_argument(
        "--local-config",
        type=Path,
        help="Local film paths JSON (default: $EDITARR_EVAL_FILMS_LOCAL, else "
        "<data root>/films.local.json)",
    )

    def selection(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--only-folder", help="Path prefix, e.g. video/movies_fight")
        sub.add_argument("--limit", type=int, help="Process at most N pending items")

    def downloads(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--allow-downloads",
            action="store_true",
            help="Let Editarr-managed models download missing artifacts (default: use "
            "installed artifacts only and report others unavailable)",
        )

    frames = commands.add_parser("frames", help="Extract frames with ffmpeg (resumable)")
    selection(frames)
    frames.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))

    run = commands.add_parser("run", help="Score frames with one model (resumable)")
    run.add_argument("--model", required=True, help="Model id, e.g. nudenet or siglip2")
    run.add_argument("--retry-errors", action="store_true", help="Re-score errored items")
    run.add_argument(
        "--subset", help="Only score a subset's frames (any name in _results/subsets/)"
    )
    run.add_argument(
        "--concurrency",
        type=int,
        help="Items scored in parallel (decision models only; default: per-model setting)",
    )
    selection(run)
    downloads(run)

    sample = commands.add_parser("sample", help="Write subsets/decision_sample.json")
    sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    film_subset = commands.add_parser(
        "film-subset",
        help=f"Write a stratified subset of decision_sample film windows ({FILMS_QWEN300})",
    )
    film_subset.add_argument("--name", default=FILMS_QWEN300)
    film_subset.add_argument("--size", type=int, default=300)
    film_subset.add_argument("--seed", type=int, default=2026)
    film_subset.add_argument("--min-per-key", type=int, default=30)
    commands.add_parser("summarize", help="Write summary.json and models/index.json")
    describe = commands.add_parser(
        "describe-eval",
        help="Judge descriptions (d1-3B, text only) and severity; write describe_eval.json",
    )
    describe.add_argument(
        "--subset", default=FILMS_QWEN300, help=f"Subset name (default: {FILMS_QWEN300})"
    )
    describe.add_argument("--models", help="Comma-separated model ids (default: all)")
    commands.add_parser("viewer", help="Copy the static viewer into _results/")
    serve_parser = commands.add_parser(
        "serve", help="Serve evaluation_data on 127.0.0.1 with the corrections API"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8765, help="Port on 127.0.0.1 (0 picks a free port)"
    )

    everything = commands.add_parser("all", help="inventory, frames, run models, summarize, viewer")
    everything.add_argument(
        "--models",
        default=",".join([*LOCAL_BACKENDS, *DECISION_MODEL_IDS]),
        help="Comma-separated model ids (unavailable ones are skipped)",
    )
    everything.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    selection(everything)
    downloads(everything)
    return parser


@contextmanager
def _sigterm_exits() -> Iterator[None]:
    """Turn SIGTERM into SystemExit for the block, so `finally` clauses close backends
    (stopping llama-server and freeing model memory) instead of orphaning them."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    with _sigterm_exits():
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    layout = Layout((args.data_root or default_data_root()).resolve())
    prefix = normalize_prefix(getattr(args, "only_folder", None))
    limit = getattr(args, "limit", None)
    if args.command in {"run", "all"}:
        # Process-wide, before any model thread starts; see configure_model_downloads.
        configure_model_downloads(args.allow_downloads)

    if args.command in {"inventory", "all"}:
        write_inventory(layout, LabelRules.load(args.rules))
    if args.command == "films":
        try:
            sources = resolve_sources(
                layout,
                ground_truth_dir=args.ground_truth_dir,
                film_files=parse_film_options(args.film),
                local_config=args.local_config,
            )
            build_films(layout, sources, args.config)
        except FilmSourceError as exc:
            log.error("%s", exc)
            return 2
    if args.command in {"frames", "all"}:
        extract_frames(layout, only_folder=prefix, limit=limit, workers=args.workers)
    if args.command == "sample":
        write_decision_sample(layout, args.seed)
    if args.command == "film-subset":
        write_film_subset(
            layout, args.name, size=args.size, seed=args.seed, min_per_key=args.min_per_key
        )
    if args.command == "run":
        try:
            run_model(
                layout, args.model, limit=limit, only_folder=prefix,
                retry_errors=args.retry_errors,
                subset=load_subset(layout, args.subset) if args.subset else None,
                concurrency=args.concurrency,
            )
        except BackendUnavailable as exc:
            log.error("%s", exc)
            return 2
    if args.command == "all":
        for model_id in filter(None, (value.strip() for value in args.models.split(","))):
            try:
                run_model(layout, model_id, limit=limit, only_folder=prefix)
            except BackendUnavailable as exc:
                log.warning("Skipping %s: %s", model_id, exc)
    if args.command == "describe-eval":
        from scanner.evaluation.folder_eval.describe_eval import describe_eval

        models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
        describe_eval(layout, args.subset, models)
        log.info("Wrote %s", layout.results / "describe_eval.json")
    if args.command in {"summarize", "all"}:
        summarize(layout)
        log.info("Wrote %s", layout.summary)
    if args.command in {"viewer", "all"}:
        install_viewer(layout)
    if args.command == "serve":
        from scanner.evaluation.folder_eval.server import serve

        serve(layout, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
