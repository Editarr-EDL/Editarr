"""Launch the buildless, local Qwen language replay interface."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from scanner.http.qwen_language_replay_web import DEFAULT_PORT, create_app
from scanner.model_acquisition import AcquisitionCancelled, _cross_process_lock
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.config import settings


def main() -> None:
    """Serve the replay page with its saved clip and reports."""
    parser = argparse.ArgumentParser(description="Open a local web UI for Qwen language replay")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cache-dir", type=Path, help="Override the default transcript cache path")
    parser.add_argument("--session-dir", type=Path, help="Override the replay session directory")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    repo_dir = Path(__file__).resolve().parents[2]
    index = repo_dir / "web" / "src" / "settings" / "qwen-language-replay" / "index.html"
    if not index.is_file():
        parser.error("Run this command from an Editarr source checkout")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session_dir = (
        args.session_dir or settings.cache_dir / "qwen-language-replays" / f"web-{args.port}"
    )
    session_dir = ensure_user_private_directory(session_dir.resolve())
    try:
        # Refuse a second launcher if another process owns this session directory.
        with _cross_process_lock(session_dir / ".server.lock", lambda: True):
            app = create_app(
                repo_dir=repo_dir,
                session_dir=session_dir,
                port=args.port,
                cache_dir=args.cache_dir.resolve() if args.cache_dir is not None else None,
            )
            uvicorn.run(
                app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False
            )
    except AcquisitionCancelled:
        parser.error("The replay session directory is already in use by another server")


if __name__ == "__main__":
    main()
