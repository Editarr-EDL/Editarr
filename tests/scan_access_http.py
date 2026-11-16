"""MU-21 HTTP harness: the real scan routers behind the access middleware.

Accounts come from `live_accounts` (`mu04-access-builders-3`): owner 1, admins
2 and 3, users 4 and 5, each with a session and an API key, all local. Jellyfin
item reads, the scheduler wake-up, and worker termination are replaced and
record their calls. Scans are seeded with `seed_scan`, which stores the same
rows a submission would, under synthetic files in the test directory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

import scanner.api as root
from scanner.db import (
    MediaAccessScopeRow,
    MediaAccessScopeSourceRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
    ScanQueueRow,
    ScanRow,
    UserRow,
)
from scanner.http import credits_api, scan_create_api, scan_jobs_api
from scanner.models import JellyfinMediaItem, JellyfinSettingsUpdate
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.repository import (
    create_scan,
    enqueue_scan,
    update_jellyfin_settings,
    upsert_media_item,
)
from scanner.security.access.identity import UserId
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import LiveAccounts

JELLYFIN = "http://jellyfin.test"
LIBRARY_A = "a" * 32
LIBRARY_B = "b" * 32


def guid(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()


@dataclass
class ScanApp:
    client: object
    accounts: LiveAccounts
    media: Path
    calls: list[str] = field(default_factory=list)
    items: dict[str, JellyfinMediaItem] = field(default_factory=dict)

    def send(self, method: str, path: str, headers: dict, body=None):
        self.client.cookies.clear()
        return self.client.request(method, path, headers=headers, json=body)

    def file(self, name: str) -> Path:
        path = self.media / name
        if not path.exists():
            path.write_bytes(b"synthetic video bytes")
        return path

    def jellyfin_item(self, item_id: str, library: str) -> str:
        """Serve one synthetic Jellyfin item from the fake client; returns its GUID."""

        self.items[item_id] = JellyfinMediaItem(
            id=item_id,
            library_id=library,
            item_type="Movie",
            name=f"Movie {item_id[:4]}",
            path=str(self.file(f"{item_id}.mkv")),
            duration_seconds=60.0,
        )
        return item_id

    def seed_scan(
        self,
        initiator: int,
        name: str,
        *,
        library: str | None = LIBRARY_A,
        status: str = "queued",
        error: str | None = None,
    ) -> tuple[str, int | None]:
        """Store a scan as `initiator` with its queue row unless it is finished."""

        path = str(self.file(f"{name}.mkv").resolve())
        with self.accounts.factory() as db:
            revision = db.scalar(select(UserRow.permission_revision).where(UserRow.id == initiator))
            media = upsert_media_item(
                db,
                server_id=JELLYFIN,
                jellyfin_id=guid(name),
                library_id=library,
                item_type="Movie",
                name=name,
                path=path,
            )
            scan = create_scan(
                db,
                media_item=media,
                resolved_local_path=path,
                source_path=path,
                name=name,
                scan_audio=False,
                audio_model=None,
                audio_filter=None,
                scan_video=True,
                video_model=None,
                initiator=ActivityOwner(initiator, revision),
            )
            queue_id = None
            if status in {"queued", "scanning", "orphaned", "acquiring_model"}:
                queue_id = enqueue_scan(db, scan.id).queue_id
            row = db.get(ScanRow, scan.id)
            row.status, row.error_message = status, error
            if status == "acquiring_model":
                row.progress_percent, row.progress_message = 40.0, "Acquiring optional Qwen model"
            db.commit()
            return scan.id, queue_id

    def seed_review(self, owner: int, name: str, scan_id: str) -> str:
        review_id = f"review-{name}"
        with self.accounts.factory() as db:
            media_item_id = db.get(ScanRow, scan_id).media_item_id
            db.add(
                SavedReviewRow(
                    id=review_id,
                    media_item_id=media_item_id,
                    name=name,
                    owner_id=owner,
                    original_author_id=owner,
                )
            )
            db.add(
                ReviewScanMembershipRow(
                    review_id=review_id, scan_id=scan_id, role="both", position=0
                )
            )
            db.commit()
        return review_id

    def limit_to_library(self, user_id: int, library: str) -> None:
        """Give a local account its own scope holding only one Jellyfin library."""

        with self.accounts.factory() as db:
            scope = MediaAccessScopeRow(user_id=user_id, all_sources=False)
            db.add(scope)
            db.flush()
            db.add(
                MediaAccessScopeSourceRow(
                    scope_id=scope.id,
                    source_kind=MediaSourceKind.JELLYFIN_LIBRARY,
                    source_id=library,
                )
            )
            db.execute(
                UserRow.__table__.update()
                .where(UserRow.id == user_id)
                .values(permission_revision=UserRow.permission_revision + 1)
            )
            db.commit()

    def scan(self, scan_id: str) -> ScanRow | None:
        with self.accounts.factory() as db:
            row = db.get(ScanRow, scan_id)
            if row is not None:
                db.expunge(row)
            return row

    def queued(self) -> list[str]:
        with self.accounts.factory() as db:
            return list(
                db.scalars(
                    select(ScanQueueRow.scan_id).order_by(ScanQueueRow.sort_order, ScanQueueRow.id)
                )
            )

    def revision(self, user_id: int) -> int:
        with self.accounts.factory() as db:
            return db.scalar(
                select(UserRow.permission_revision).where(UserRow.id == UserId(user_id))
            )


class _Jellyfin:
    def __init__(self, items: dict[str, JellyfinMediaItem], calls: list[str]):
        self.items, self.calls = items, calls

    def __call__(self, *_credentials):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def item(self, item_id: str) -> JellyfinMediaItem:
        self.calls.append(f"jellyfin:{item_id}")
        return self.items[item_id]


def serve_scans(monkeypatch, accounts: LiveAccounts, tmp_path: Path) -> ScanApp:
    """Start the harness; the caller closes `app.client`."""

    media = tmp_path / "media"
    media.mkdir()
    client = serve_access_fixture(monkeypatch, accounts)
    app = ScanApp(client, accounts, media)
    monkeypatch.setattr(root, "JellyfinClient", _Jellyfin(app.items, app.calls))
    monkeypatch.setattr(
        root, "_start_scan_queue_worker_if_needed", lambda: app.calls.append("wake")
    )
    monkeypatch.setattr(
        root, "_terminate_scan_worker", lambda scan_id, *_args: app.calls.append(f"stop:{scan_id}")
    )
    monkeypatch.setattr(root, "record_jellyfin_connection", lambda *_args: None)
    for router in (scan_create_api.router, scan_jobs_api.router, credits_api.router):
        client.app.include_router(router)
    with accounts.factory() as db:
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))
    return app
