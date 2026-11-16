#!/usr/bin/env python3
"""Launch Editarr's isolated API-only and Vite development modes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from scanner.cli import _build_parser, resolve_server_launch_context
from scanner.runtime.development import resolve_development_topology


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Editarr development services")
    parser.add_argument("mode", choices=("api", "web", "dev"))
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--check", action="store_true", help="Validate and print without launching")
    return parser


def _stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _vite_bind_host(canonical_web_host: str) -> str:
    """Honor wildcard access while keeping explicit loopback launches private."""

    return "0.0.0.0" if canonical_web_host == "0.0.0.0" else "127.0.0.1"


def _run_process_pair(
    api_command: list[str],
    web_command: list[str],
    environment: dict[str, str],
    *,
    popen=subprocess.Popen,
    pause=time.sleep,
) -> int:
    """Run both dev servers and always stop the survivor when either exits."""

    processes: list[subprocess.Popen] = []
    try:
        processes.append(popen(api_command, env=environment))
        processes.append(popen(web_command, cwd="web", env=environment))
        while all(process.poll() is None for process in processes):
            pause(0.1)
        return next(process.returncode for process in processes if process.returncode is not None)
    finally:
        _stop(processes)


def main() -> int:
    args = _parser().parse_args()
    if args.mode == "api":
        cli_args = ["--api-only", "--host", args.api_host]
        if args.api_port is not None:
            cli_args.extend(("--port", str(args.api_port)))
        context = resolve_server_launch_context(
            _build_parser().parse_args(cli_args), original_argv=tuple(cli_args)
        )
        prefix = context.snapshot.prefix
        api_path = "/api" if prefix == "/" else f"{prefix}/api"
        print(
            f"API-only: {context.snapshot.public_origin.serialized}{api_path} "
            f"(listen http://{context.host}:{context.snapshot.listening_port}, "
            f"Base URL source: {context.snapshot.base_url_source}, "
            f"port source: {context.snapshot.port_source})",
            flush=True,
        )
        if args.check:
            return 0
        os.execv(sys.executable, [sys.executable, "-m", "scanner.cli", *cli_args])

    topology = resolve_development_topology(
        api_host=args.api_host,
        api_port=args.api_port,
        web_host=args.web_host,
        web_port=args.web_port,
    )
    print(
        f"Web UI: {topology.browser_url}\n"
        f"API proxy: {topology.browser_origin}{topology.api_path} -> "
        f"{topology.api_target}{topology.api_path}\n"
        f"Base URL source: {topology.snapshot.base_url_source}; "
        f"API port source: {topology.snapshot.port_source}",
        flush=True,
    )
    if args.mode == "web":
        print(
            "Web-only mode expects mocked APIs; use 'dev' for an authenticated live API.",
            flush=True,
        )
    if args.check:
        return 0

    environment = topology.child_environment(os.environ)
    web_command = [
        "pnpm",
        "dev",
        "--host",
        _vite_bind_host(args.web_host),
        "--port",
        str(args.web_port),
        "--strictPort",
    ]
    if args.mode == "web":
        return subprocess.call(web_command, cwd="web", env=environment)

    api_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "scanner.runtime.development:create_development_app",
        "--factory",
        "--reload",
        "--no-proxy-headers",
        "--host",
        args.api_host,
        "--port",
        str(topology.snapshot.listening_port),
    ]
    try:
        return _run_process_pair(api_command, web_command, environment)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
