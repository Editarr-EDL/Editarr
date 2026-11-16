"""Naming is display metadata across source admission, workers, and provenance."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scanner import api, repository
from scanner.db import (
    Base,
    MediaItemExportRow,
)
from scanner.models import (
    ExportFormat,
    ScanOrigin,
)
from scanner.security.auth_repository import issue_session
from scanner.settings.config import Settings
from scanner.settings.deployment_config import parse_base_url
from tests.multi_user_access_support import add_linked_owner


def _mcf_import_options(name, target, *, start_seconds=0.0, end_seconds=100.0, ignore_errors=False):
    return {
        "name": name,
        "target": target,
        "synchronization": {
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "end_mode": "before_credits",
        },
        "mcf_material_end_seconds": end_seconds,
        "accepted_credits": None,
        "ignore_errors": ignore_errors,
    }


def upload_import(client, kind, options, content, *, filename=None, **request):
    """Import an EDL or MCF file the way the browser does; it is the only import transport."""
    return client.post(
        f"/api/{kind}-imports/upload",
        files={"file": (filename or f"input.{kind}", content, "text/plain")},
        data={"options": json.dumps(options)},
        **request,
    )


@pytest.fixture
def db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scan-naming.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(repository, "compute_video_md5", lambda _path: "test-hash")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "require_available", lambda _model: None)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def client_context(db, monkeypatch, tmp_path):
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("scanner.security.auth_policy.get_session_factory", lambda: factory)
    parsed = parse_base_url("http://testserver")
    monkeypatch.setattr(
        api.app.state,
        "deployment_snapshot",
        replace(
            api.app.state.deployment_snapshot,
            base_url=parsed.base_url,
            public_origin=parsed.public_origin,
            prefix="",
            cookie_secure=False,
            session_not_before=None,
        ),
    )
    add_linked_owner(db, "Admin")
    issued = issue_session(db, remember_me=False, configuration=Settings(data_dir=tmp_path))

    def test_db():
        yield db

    monkeypatch.setitem(api.app.dependency_overrides, api.get_db, test_db)
    # Do not run the application lifespan or launch any queue worker.
    client = TestClient(api.app)
    headers = {
        "Cookie": f"{api.settings.auth_cookie_name}={issued.token}",
        "Origin": "http://testserver",
        "X-CSRF-Token": issued.csrf_token,
    }
    yield client, headers
    client.close()


def published_bytes(db, review_id, format):
    """Compare canonical disk and retained bytes for a review publication."""
    export = (
        db.query(MediaItemExportRow)
        .filter_by(review_id=review_id, format=ExportFormat(format))
        .one()
    )
    destination = Path(export.destination_path)
    assert destination.parent.parent.name == format
    assert destination.suffix == f".{format}"
    assert destination.read_bytes() == export.content_bytes
    return export.content_bytes


def create_test_scan(db, *, name=None, origin=ScanOrigin.GENERATED):
    item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/test-only/example.mkv",
    )
    return repository.create_scan(
        db,
        media_item=item,
        resolved_local_path=item.path,
        name=name,
        origin=origin,
        media_provider_id="provider-1",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
    )
