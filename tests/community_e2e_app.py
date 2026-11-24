"""Production Editarr HTTP application with an isolated loopback community ingress."""

import asyncio
import functools
import hashlib
import json
import os
import socket
import sys
import uuid
from pathlib import Path

import httpx
import uvicorn

from scanner.http import community_identity_api, jellyfin_setup_api
from scanner.integrations.community.transport import CommunityTransport
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.jellyfin_accounts import sync
from tests.community_identity_support import identity_home
from tests.jellyfin_setup_fake import FakeJellyfin


class LoopbackCommunityTransport(httpx.AsyncBaseTransport):
    """Forward original signed bytes to real HTTP without changing their authority."""

    def __init__(self):
        self.network = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        assert request.url.host == "community.example"
        body = await request.aread()
        root = Path(os.environ["EDITARR_DATA_DIR"])
        with (root / "community-wire.jsonl").open("a") as log:
            log.write(
                json.dumps(
                    {
                        "method": request.method,
                        "path": request.url.path,
                        "operation": request.headers.get("idempotency-key"),
                        "digest": hashlib.sha256(body).hexdigest(),
                    }
                )
                + "\n"
            )
        local = httpx.URL(os.environ["COMMUNITY_E2E_LOCAL"])
        forwarded = httpx.Request(
            request.method,
            request.url.copy_with(scheme="http", host=local.host, port=local.port),
            headers=request.headers,
            content=body,
            extensions=request.extensions,
        )
        forwarded.headers["CF-Connecting-IP"] = "192.0.2.100"
        response = await self.network.handle_async_request(forwarded)
        fault_path = root / "community-fault.json"
        if fault_path.exists():
            fault = json.loads(fault_path.read_text())
            if request.url.path == fault["path"] and fault["remaining"] > 0:
                fault["remaining"] -= 1
                fault_path.write_text(json.dumps(fault))
                await response.aread()
                await response.aclose()
                raise httpx.ReadError("Synthetic response loss after real service processing")
        return response

    async def aclose(self):
        await self.network.aclose()


def transport(endpoint):
    return CommunityTransport(endpoint, adapter=LoopbackCommunityTransport())


def finish_queued_scans():
    """Run the real scan pipeline with deterministic model output, never model downloads."""
    from scanner import repository
    from scanner.db import ScanRow, get_session_factory
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.models import FindingCreate
    from scanner.persistence.repository.repository_audio_tracks import (
        record_analyzed_audio_selection,
    )
    from scanner.pipeline import runner

    def visual(_path, scan_id, *_args, **_kwargs):
        return [
            FindingCreate(
                video_id=scan_id,
                source="visual",
                category="violence",
                subcategory="violence",
                start_seconds=0.5,
                end_seconds=1.0,
                severity="medium",
                confidence=0.9,
                label="Synthetic visual finding",
                edit_action="skip",
                selected=True,
            )
        ]

    runner._scan_visual_isolated = visual
    runner._scan_audio_isolated = lambda _path, scan_id, *_args, **_kwargs: [
        FindingCreate(
            video_id=scan_id,
            source="audio",
            category="language",
            subcategory="swearing",
            start_seconds=0.25,
            end_seconds=0.5,
            severity="medium",
            confidence=0.9,
            label="Synthetic audio finding",
            edit_action="mute",
            selected=True,
        )
    ]
    with get_session_factory()() as db:
        queued = repository.get_next_scan_queue(db)
        if queued is not None:
            scan = db.get(ScanRow, queued.id)
            selection = (
                AudioSelection.model_validate_json(scan.requested_audio_selection_json).selection()
                if scan.requested_audio_selection_json
                else None
            )
            runner.run_scan(
                db,
                scan.resolved_local_path,
                video_id=queued.id,
                scan_audio=scan.scan_audio,
                scan_video=scan.scan_video,
                video_model="nudenet",
                audio_selection=selection,
                analyzed_audio_callback=lambda actual: record_analyzed_audio_selection(
                    db, scan.id, AudioSelection.from_selection(actual)
                ),
                status_updater=repository.update_scan_status,
                findings_writer=repository.create_scan_findings_batch,
            )
            repository.remove_scan_queue_item(db, queued.queue_id)


JELLYFIN_ITEM = "e2e-synthetic-movie"


class SyntheticJellyfin:
    """A Jellyfin server whose one movie is this installation's generated media file."""

    def __init__(self, *_credentials):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def item(self, item_id):
        from scanner.integrations.jellyfin import JellyfinNotFound
        from scanner.modeling.media_identity import normalize_jellyfin
        from scanner.models import JellyfinMediaItem

        if item_id != JELLYFIN_ITEM:
            raise JellyfinNotFound("Synthetic Jellyfin has one item")
        record = {"Type": "Movie", "Id": item_id, "ProviderIds": {"Tmdb": "123"}}
        return JellyfinMediaItem(
            id=item_id,
            library_id="movies",
            item_type="Movie",
            name="Synthetic movie",
            path=str(Path(os.environ["EDITARR_DATA_DIR"]) / "synthetic.mp4"),
            media_identity=normalize_jellyfin(record),
        )


def serve_synthetic_jellyfin(api):
    from scanner.integrations.community import find_targets

    api.JellyfinClient = SyntheticJellyfin
    find_targets.JellyfinClient = SyntheticJellyfin
    api._test_jellyfin_connection = lambda *_args: None
    jellyfin = FakeJellyfin()
    jellyfin_setup_api.setup_transport = jellyfin.transport()
    transport = jellyfin.transport()
    sync.JellyfinUserAdapter = functools.partial(JellyfinUserAdapter, transport=transport)


class ReadyServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            print(json.dumps({"port": sockets[0].getsockname()[1]}), flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "master":
        from tests.community_e2e_masters import run

        run()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "request":
        from scanner.integrations.community.transport import ServiceEndpoint
        from scanner.security.community_identity import load_or_create_installation_identity

        value = json.load(sys.stdin)
        identity = load_or_create_installation_identity(
            identity_home(Path(os.environ["EDITARR_DATA_DIR"]))
        )
        result = asyncio.run(
            transport(ServiceEndpoint("https://community.example", "community.example")).request(
                value["method"],
                value["path"],
                identity=identity,
                body=json.dumps(value["body"], separators=(",", ":")).encode()
                if value["body"] is not None
                else b"",
                operation_id=value.get("operation_id")
                or (str(uuid.uuid4()) if value["method"] != "GET" else None),
            )
        )
        print(json.dumps(result))
        return
    community_identity_api.CommunityTransport = transport
    from scanner import api

    api._start_scan_queue_worker_if_needed = finish_queued_scans
    serve_synthetic_jellyfin(api)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        ReadyServer(uvicorn.Config(api.app, log_level="error")).run(sockets=[listener])


if __name__ == "__main__":
    main()
