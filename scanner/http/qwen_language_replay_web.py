"""Loopback-only browser endpoints for replaying local Qwen language prompts."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from scanner.evaluation.qwen_language_comparison import align_language_replay_findings
from scanner.evaluation.qwen_language_replay import (
    format_language_replay_diff,
    language_replay_setting_changes,
    read_comparable_replays,
)
from scanner.pipeline.audio_tracks import probe_audio_tracks

from .qwen_language_replay_inspection import InspectionRunner, register_inspection_routes
from .qwen_language_replay_process import ReplayProcess, ReplayRunner, RunRequest, Slot
from .qwen_language_replay_session import ReplayClip, ReplaySession, finding_view

DEFAULT_PORT = 8767
UPLOAD_LIMIT = 512 * 1024 * 1024
_AUDIO_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wave",
}
_CSP = "default-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'"


class AudioPathRequest(BaseModel):
    """Select one explicitly provided local audio file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


def _audio_name(header: str) -> str:
    label = unquote(header).replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in label if char.isprintable()).strip()[:200] or "Audio clip"


def _local_audio_file(raw_path: str) -> tuple[Path, int]:
    """Resolve and verify a regular audio file without copying or decoding it."""
    from av import FFmpegError

    selection = Path(raw_path)
    if not selection.is_absolute() and not raw_path.startswith("~/"):
        raise ValueError("Enter an absolute audio path or a path starting with ~/.")
    if selection.suffix.lower() not in _AUDIO_SUFFIXES:
        raise ValueError("Choose a supported audio file, such as WAV, FLAC, or MP3.")
    try:
        path = selection.expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise OSError("Not a readable regular file")
        size = path.stat().st_size
        tracks = probe_audio_tracks(path).tracks
    except (FFmpegError, OSError, RuntimeError, ValueError) as error:
        raise ValueError("The local path must point to a readable audio file.") from error
    if not tracks:
        raise ValueError("The selected file does not contain audio.")
    return path, size


async def _store_audio(request: Request, destination: Path, limit: int) -> int:
    """Stream an audio upload to a private session file under the byte limit."""
    mebibytes, remainder = divmod(limit, 1024**2)
    max_size = f"{limit:,} bytes" if remainder else f"{mebibytes} MiB"
    size_error = f"Audio clips must be no larger than {max_size}."
    length = request.headers.get("content-length")
    if length and length.isdecimal() and int(length) > limit:
        raise HTTPException(413, size_error)
    size = 0
    with destination.open("wb") as stream:
        async for block in request.stream():
            size += len(block)
            if size > limit:
                raise HTTPException(413, size_error)
            stream.write(block)
    if not size:
        raise HTTPException(400, "Choose an audio file that contains audio.")
    return size


def create_app(
    *,
    repo_dir: Path,
    session_dir: Path,
    port: int = DEFAULT_PORT,
    cache_dir: Path | None = None,
    upload_limit: int = UPLOAD_LIMIT,
    runner: ReplayRunner | None = None,
    inspection_runner: InspectionRunner | None = None,
) -> FastAPI:
    """Serve the static replay tool and manage one clip and pair of local reports."""
    host_with_port = f"127.0.0.1:{port}"
    host = "127.0.0.1" if port == 80 else host_with_port
    origin = f"http://{host}"
    assets = repo_dir / "web" / "src"
    feature = assets / "settings" / "qwen-language-replay"
    process = ReplayProcess(repo_dir, cache_dir)
    session = ReplaySession(session_dir, runner if runner is not None else process, upload_limit)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await asyncio.to_thread(process.close)
        if session.worker is not None:
            await asyncio.to_thread(session.worker.join, 5)

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_requests(request: Request, next_response):
        if request.headers.get("host") not in {host, host_with_port}:
            return JSONResponse({"detail": "Use the local replay server address."}, status_code=403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("origin") != origin:
            return JSONResponse({"detail": "Use the replay page to make changes."}, status_code=403)
        response = await next_response(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": _CSP,
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
            }
        )
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(feature / "index.html", media_type="text/html")

    @app.get("/replay.js")
    def script() -> FileResponse:
        return FileResponse(feature / "replay.js", media_type="text/javascript")

    @app.get("/replay-inspector.js")
    def inspector_script() -> FileResponse:
        return FileResponse(feature / "replay-inspector.js", media_type="text/javascript")

    @app.get("/replay.css")
    def stylesheet() -> FileResponse:
        return FileResponse(assets / "styles" / "qwen-language-replay.css", media_type="text/css")

    @app.get("/api/status")
    def status() -> dict:
        with session.lock:
            return session.snapshot()

    @app.put("/api/audio")
    async def upload(request: Request) -> dict:
        with session.lock:
            session.require_idle()
            session.changing_audio = True
        pending = session_dir / ".clip-upload"
        try:
            size = await _store_audio(request, pending, upload_limit)
            name = _audio_name(request.headers.get("x-audio-name", ""))
            with session.lock:
                return session.select_audio(ReplayClip(name=name, size_bytes=size), upload=pending)
        finally:
            try:
                pending.unlink(missing_ok=True)
            finally:
                with session.lock:
                    session.changing_audio = False

    @app.put("/api/audio-path")
    def select_local_audio(options: AudioPathRequest) -> dict:
        with session.lock:
            session.require_idle()
            session.changing_audio = True
        try:
            try:
                path, size = _local_audio_file(options.path)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            with session.lock:
                if session.selection and path.is_relative_to(session.work_dir.resolve()):
                    raise HTTPException(400, "Choose the original audio file on disk.")
                return session.select_audio(
                    ReplayClip(name=path.name, size_bytes=size, source="local"), local_path=path
                )
        finally:
            with session.lock:
                session.changing_audio = False

    @app.post("/api/run", status_code=202)
    def run(options: RunRequest) -> dict:
        with session.lock:
            session.require_idle()
            if session.selection is None:
                raise HTTPException(409, "Choose an audio clip first.")
            if not session.audio_path.is_file():
                raise HTTPException(409, "The selected audio file is unavailable.")
            session.error = None
            session.active = {"slot": options.slot, "message": "Starting replay…"}
            session.worker = Thread(target=session.run, args=(options,), daemon=True)
            session.worker.start()
            return session.snapshot()

    @app.get("/api/compare")
    def compare() -> dict:
        with session.lock:
            if not all(session.reports.values()):
                raise HTTPException(409, "Run a baseline and candidate first.")
            try:
                baseline, candidate = read_comparable_replays(
                    session.report_path("baseline"), session.report_path("candidate")
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        rows = align_language_replay_findings(
            baseline.findings,
            candidate.findings,
            baseline_padding_seconds=baseline.audio_padding_seconds,
            candidate_padding_seconds=candidate.audio_padding_seconds,
        )
        return {
            "rows": [
                {
                    "timestamp_seconds": row.timestamp_seconds,
                    "baseline": finding_view(row.baseline) if row.baseline is not None else None,
                    "candidate": finding_view(row.candidate) if row.candidate is not None else None,
                }
                for row in rows
            ],
            "notes": language_replay_setting_changes(baseline, candidate),
            "baseline_count": len(baseline.findings),
            "candidate_count": len(candidate.findings),
            "diff": format_language_replay_diff(
                baseline, candidate, baseline_label="Baseline", candidate_label="Candidate"
            ),
        }

    @app.get("/api/reports/{slot}")
    def report(slot: Slot) -> Response:
        with session.lock:
            if session.reports[slot] is None:
                raise HTTPException(404, "This replay report is still empty.")
            data = session.report_path(slot).read_bytes()
        return Response(
            data,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="qwen-{slot}.json"'},
        )

    inspector = inspection_runner if inspection_runner is not None else process.inspect
    register_inspection_routes(app, session, cache_dir, inspector)
    return app
