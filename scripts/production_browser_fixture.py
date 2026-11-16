#!/usr/bin/env python3
"""Keep one disposable, installed-artifact TLS fixture alive for Playwright.

The JSON-lines control channel listens only on loopback and is deliberately not an
Editarr endpoint.  It exists so the browser test can update its *test-owned* proxy
after staging a new prefix or upstream listener.  It never changes a user proxy,
database, certificate store, or process.

Setup and sign-in go to the loopback Jellyfin stand-in from
`tests/jellyfin_setup_fake.py`, which also serves the fixture item's artwork, so
run this script with the checkout on `PYTHONPATH`.
"""

from __future__ import annotations

import argparse
import base64
import json
import socketserver
import sqlite3
import ssl
import sys
import threading
import time
import wave
from pathlib import Path

import httpx
from proxy_fixture import ProxyFixture

from tests.jellyfin_setup_fake import ADMIN, ADMIN_PASSWORD, FakeJellyfin, serve
from tests.jellyfin_user_boundary import IDS, PRIMARY, user_dto

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9JtU8AAAAASUVORK5CYII="
)
_CHROMIUM_SPKI_FLAG = "--ignore-certificate-errors-spki-list="


_LIBRARY, _ITEM, _VERSION = "e1" * 16, "e2" * 16, "e3" * 16
_TICKS = 2 * 10_000_000


def _catalog(path: str, query: httpx.QueryParams, media: Path) -> object | None:
    """The one-movie library Editarr reads with its minted key, as Jellyfin shapes it."""
    if path == "/Library/VirtualFolders":
        return [
            {
                "ItemId": _LIBRARY,
                "Name": "Fixture Movies",
                "CollectionType": "movies",
                "Locations": [str(media.parent)],
            }
        ]
    if path == "/Items":
        ids = query.get("Ids")
        version = {"Id": _VERSION, "Name": "Fixture version", "Path": str(media)}
        item = {
            "Id": _ITEM,
            "LibraryId": _LIBRARY,
            "ParentId": _LIBRARY,
            "Name": "Fixture media",
            "Type": "Movie",
            "Path": str(media),
            "RunTimeTicks": _TICKS,
            "Container": "wav",
            "MediaSourceCount": 1,
            "MediaSources": [{**version, "Container": "wav", "RunTimeTicks": _TICKS}],
            "ImageTags": {},
        }
        items = [item] if ids is None or _ITEM in ids.split(",") else []
        return {"Items": items, "TotalRecordCount": len(items)}
    if path == "/Users":
        return [user_dto(ADMIN)]
    if path == f"/Users/{IDS[ADMIN]}":
        return user_dto(ADMIN)
    return None


def _jellyfin_stand_in(media: Path) -> FakeJellyfin:
    """The setup stand-in; its minted key also reads the fixture library and artwork."""
    fake = FakeJellyfin()
    answer = fake._answer

    def with_catalog(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/Items/fixture-item/Images/Primary"):
            return httpx.Response(200, content=_PNG, headers={"Content-Type": "image/png"})
        if fake._token(request) in fake.editarr_keys():
            body = _catalog(request.url.path, request.url.params, media)
            if body is not None:
                return httpx.Response(200, json=body)
        return answer(request)

    fake._answer = with_catalog  # type: ignore[method-assign]
    return fake


class _ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, fixture: ProxyFixture, secondary: ProxyFixture, finding: dict[str, int]):
        self.fixture = fixture
        self.secondary = secondary
        self.finding = finding
        super().__init__(("127.0.0.1", 0), _ControlHandler)


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline())
            if request.get("action") == "reconfigure":
                self.server.fixture.reconfigure(  # type: ignore[attr-defined]
                    upstream_port=request.get("upstream_port"), prefix=request.get("prefix")
                )
                result = {"ok": True}
            elif request.get("action") == "reconfigure_secondary":
                self.server.secondary.reconfigure(  # type: ignore[attr-defined]
                    upstream_port=request.get("upstream_port"), prefix=request.get("prefix")
                )
                _wait_secondary_ready(self.server.secondary)  # type: ignore[attr-defined]
                result = {"ok": True}
            elif request.get("action") == "wait_ready":
                self.server.fixture.wait_ready()  # type: ignore[attr-defined]
                result = {"ok": True}
            elif request.get("action") == "status":
                fixture = self.server.fixture  # type: ignore[attr-defined]
                result = {
                    "ok": True,
                    "prefix": fixture.prefix,
                    "upstream_port": fixture.upstream_port,
                }
            elif request.get("action") == "fixture_media":
                result = {"ok": True, **self.server.finding}  # type: ignore[attr-defined]
            elif request.get("action") == "backend_log":
                result = {
                    "ok": True,
                    "log": self.server.fixture.backend_log.read_text(errors="replace")[-4_000:],  # type: ignore[attr-defined]
                }
            else:
                result = {"ok": False, "error": "unknown fixture action"}
        except Exception as error:  # surface only test-fixture failures to the runner
            result = {"ok": False, "error": str(error)}
        self.wfile.write((json.dumps(result) + "\n").encode())


def _fixture_media(data: Path) -> Path:
    """Write the tiny WAV the stand-in's library holds."""
    source = data / "media" / "fixture-preview.wav"
    source.parent.mkdir()
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\0\0" * 32_000)
    return source


def _seed_fixture_media(data: Path, source: Path) -> dict[str, int | str]:
    """Seed only disposable sqlite rows for the stand-in's movie; setup connects the Jellyfin."""
    database = sqlite3.connect(data / "scanner.db")
    try:
        database.execute(
            """INSERT INTO media_items (id, server_id, jellyfin_id, library_id, item_type,
            name, path, created_at, updated_at) VALUES ('fixture-media', ?, ?, ?, 'Movie',
            'Fixture media', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (PRIMARY, _ITEM, _LIBRARY, str(source)),
        )
        database.execute(
            """INSERT INTO scans (id, media_item_id, scan_number, source_path, media_source_id,
                resolved_local_path, status, scan_audio, scan_video, speed_up, qwen_model,
                qwen_summaries_per_segment,
                qwen_segment_join_gap_seconds, selection_revision, progress_percent,
                progress_stage, progress_message, visual_checkpoint_complete,
                audio_extraction_checkpoint_complete, audio_checkpoint_complete,
                checkpoint_stage, checkpoint_state, created_at, updated_at)
                VALUES ('fixture-scan', 'fixture-media', 1, ?, ?, ?, 'complete', 1, 0, 0,
                'qwen3-vl-8b-instruct-4bit', 4,
                3.0, 0, 100, 'complete', 'Fixture complete', 1, 1, 1, 'complete', '{}',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (str(source), _VERSION, str(source)),
        )
        cursor = database.execute(
            """INSERT INTO scan_findings (scan_id, category, subcategory, severity,
                mcf_channel, start_seconds, end_seconds, confidence, label, source,
                selected, edit_action, created_at)
                VALUES ('fixture-scan', 'language', 'swearing', 'medium', 'audio', 0, 1,
                1, 'Fixture audio', 'audio', 1, 'mute', CURRENT_TIMESTAMP)"""
        )
        finding_id = cursor.lastrowid
        database.execute(
            """INSERT INTO saved_reviews (id, media_item_id, media_source_id, name,
                revision, content_revision, edl_content_revision, mcf_content_revision,
                created_at, updated_at)
                VALUES ('fixture-review', 'fixture-media', ?, 'Fixture review', 0, 0, 0,
                0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (_VERSION,),
        )
        database.execute(
            """INSERT INTO review_synchronizations (review_id, start_seconds, end_seconds,
                end_mode) VALUES ('fixture-review', 0.0, 2.0, 'file_end')"""
        )
        database.execute(
            """INSERT INTO review_scan_memberships (review_id, scan_id, role, position)
                VALUES ('fixture-review', 'fixture-scan', 'audio', 0)"""
        )
        database.execute(
            """INSERT INTO review_filters (review_id, id, source_scan_id,
                source_finding_id, position, source, category, subcategory, severity,
                mcf_channel, start_seconds, end_seconds, confidence, label, edit_action,
                selected)
                VALUES ('fixture-review', 'fixture-filter', 'fixture-scan', ?, 0, 'audio',
                'language', 'swearing', 'medium', 'audio', 0, 1, 1, 'Fixture audio',
                'mute', 1)""",
            (finding_id,),
        )
        database.commit()
        return {"review_id": "fixture-review", "filter_id": "fixture-filter"}
    finally:
        database.close()


def _wait_secondary_ready(fixture: ProxyFixture) -> None:
    """Wait only for the owned second proxy/backend route after a restart."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if fixture.request(f"{fixture.prefix}/api/auth/status")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Secondary proxy readiness timed out")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editarr", type=Path, required=True)
    parser.add_argument("--dependency-site-packages", type=Path)
    parser.add_argument("--proxy", choices=("nginx", "caddy"), default="nginx")
    parser.add_argument("--prefix", default="/")
    parser.add_argument("--log-dir", type=Path, help="Keep timestamped backend and proxy logs here")
    args = parser.parse_args()

    fixture = ProxyFixture(
        args.proxy,
        args.editarr,
        args.dependency_site_packages,
        prefix=args.prefix,
        log_directory=args.log_dir,
        label="primary",
    )
    preview_renderer = fixture.root / "fixture-ffmpeg"
    preview_renderer.write_text("#!/bin/sh\nprintf 'ID3fixture-preview'\n")
    preview_renderer.chmod(0o700)
    fixture.extra_environment["IMAGEIO_FFMPEG_EXE"] = str(preview_renderer)
    with fixture:
        media = _fixture_media(fixture.data)
        with serve(_jellyfin_stand_in(media)) as (_jellyfin, jellyfin_url):
            finding = _seed_fixture_media(fixture.data, media)
            secondary = ProxyFixture(
                args.proxy,
                args.editarr,
                args.dependency_site_packages,
                log_directory=args.log_dir,
                label="secondary",
            )
            try:
                secondary._certificates()
                secondary.context = ssl.create_default_context(cafile=str(secondary.ca))
                secondary.reconfigure(upstream_port=fixture.upstream_port, prefix="/")
                with _ControlServer(fixture, secondary, finding) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    primary_certificate_arg = fixture.chromium_certificate_arg()
                    secondary_certificate_arg = secondary.chromium_certificate_arg()
                    if not (
                        primary_certificate_arg.startswith(_CHROMIUM_SPKI_FLAG)
                        and secondary_certificate_arg.startswith(_CHROMIUM_SPKI_FLAG)
                    ):
                        raise RuntimeError("Fixture returned an invalid Chromium SPKI argument")
                    certificate_arg = _CHROMIUM_SPKI_FLAG + ",".join(
                        (
                            primary_certificate_arg.removeprefix(_CHROMIUM_SPKI_FLAG),
                            secondary_certificate_arg.removeprefix(_CHROMIUM_SPKI_FLAG),
                        )
                    )
                    print(
                        json.dumps(
                            {
                                "base_url": fixture.origin,
                                "prefix": fixture.prefix,
                                "control_port": server.server_address[1],
                                "certificate_arg": certificate_arg,
                                "secondary_base_url": secondary.origin,
                                "upstream_port": fixture.upstream_port,
                                "proxy": fixture.kind,
                                "jellyfin_url": jellyfin_url,
                                "jellyfin_username": ADMIN,
                                "jellyfin_password": ADMIN_PASSWORD,
                            }
                        ),
                        flush=True,
                    )
                    try:
                        # The parent runner owns stdin and closes it during teardown.
                        for _line in sys.stdin:
                            pass
                    finally:
                        server.shutdown()
                        thread.join(timeout=5)
            finally:
                secondary.close()


if __name__ == "__main__":
    main()
