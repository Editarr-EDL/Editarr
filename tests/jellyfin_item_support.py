"""A configured Jellyfin server whose catalog serves synthetic items backed by local files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import scanner.api as root
from scanner.models import JellyfinMediaItem, JellyfinSettingsUpdate
from scanner.repository import update_jellyfin_settings

JELLYFIN_SERVER = "http://jellyfin.test"


@dataclass
class JellyfinCatalog:
    items: dict[str, JellyfinMediaItem] = field(default_factory=dict)

    def add(
        self,
        path: Path | str,
        *,
        item_id: str = "movie",
        item_type: str = "Movie",
        name: str = "Movie",
        library_id: str | None = "movies",
        **fields,
    ) -> dict:
        """Serve `path` as Jellyfin item `item_id`; returns its review target."""

        self.items[item_id] = JellyfinMediaItem(
            id=item_id,
            library_id=library_id,
            item_type=item_type,
            name=name,
            path=str(path),
            **fields,
        )
        return {"source": "jellyfin", "item_id": item_id}

    def client(self, *_credentials):
        catalog = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def item(self, item_id):
                return catalog.items[item_id]

        return Client()


def serve_jellyfin(monkeypatch, db) -> JellyfinCatalog:
    """Configure Jellyfin in `db` and replace the client with an in-memory catalog."""

    update_jellyfin_settings(
        db, JellyfinSettingsUpdate(server_url=JELLYFIN_SERVER, api_key="test-key")
    )
    catalog = JellyfinCatalog()
    monkeypatch.setattr(root, "JellyfinClient", catalog.client)
    return catalog


def jellyfin_target(monkeypatch, db, path: Path | str, **fields) -> dict:
    """Serve one Jellyfin item for `path` and return its review target."""

    return serve_jellyfin(monkeypatch, db).add(path, **fields)
