"""Live Jellyfin 12.0.0 proof that Editarr's catalog routes hide what Casey may not open.

Run from a checkout with `PYTHONPATH` set to it and scratch `EDITARR_DATA_DIR`
and `EDITARR_LOG_DIR`:

  catalog_live.py run

It reuses media_access_live.py's disposable pinned Jellyfin over synthetic
media, where Casey may open only Family and Home. A fresh migrated Editarr
database links user 4 to Casey from her verified Jellyfin snapshot, and the
real catalog routers answer HTTP through the access middleware with the real
catalog client and MU-16 evidence. Evidence goes to
web/test-results/verify-editarr/catalog-<run>/; the container and volumes are
always removed. Exit codes: 0 pass, 1 failure, 2 blocked, 64 usage.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[4]
import scanner  # noqa: E402

assert scanner.__file__.startswith(f"{CHECKOUT}/"), scanner.__file__

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_access_live as live  # noqa: E402
from media_access_live_docker import IMAGE, MARKER, cleanup, docker, save  # noqa: E402

import scanner.api as root  # noqa: E402
from scanner.db import UserProviderIdentityRow  # noqa: E402
from scanner.formats.export_layout import parse_jellyfin_user_id  # noqa: E402
from scanner.http import catalog_library_api, scan_catalog_api  # noqa: E402
from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot  # noqa: E402
from scanner.integrations.jellyfin_users import JellyfinUserAdapter  # noqa: E402
from scanner.models import JellyfinSettingsUpdate, PathMappingCreate  # noqa: E402
from scanner.repository import create_path_mapping, update_jellyfin_settings  # noqa: E402
from scanner.security.access.identity import ProviderIdentity  # noqa: E402
from scanner.security.jellyfin_accounts import reads, store  # noqa: E402
from tests.multi_user_access_http import serve_access_fixture  # noqa: E402
from tests.multi_user_access_support import OWNER, USER, live_accounts  # noqa: E402

ROOT = CHECKOUT / "web/test-results/verify-editarr"


class _Patch:
    """The monkeypatch subset `serve_access_fixture` uses, undone at exit."""

    def __init__(self) -> None:
        self.undo: list[tuple[object, str, object]] = []

    def setattr(self, target, name, value) -> None:
        self.undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def close(self) -> None:
        for target, name, value in reversed(self.undo):
            setattr(target, name, value)


def journey(base: str, key: str, casey: str, media: Path) -> dict[str, object]:
    """Link Casey, then read the catalog as the owner and as Casey over HTTP."""

    patch, scratch = _Patch(), Path(tempfile.mkdtemp(prefix="mu20-catalog-"))
    accounts = live_accounts(scratch)
    client = serve_access_fixture(patch, accounts)
    try:
        for router in (catalog_library_api.router, scan_catalog_api.router):
            client.app.include_router(router)
        with JellyfinUserAdapter(base, key) as adapter:
            server = adapter.server().server_id
            identity = ProviderIdentity(server, parse_jellyfin_user_id(casey))
            snapshot = adapter.verify_user(identity)
        assert isinstance(snapshot, JellyfinUserSnapshot), snapshot
        with accounts.factory() as db:
            update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=base, api_key=key))
            create_path_mapping(db, PathMappingCreate(remote_path="/media", local_path=str(media)))
            db.add(
                UserProviderIdentityRow(
                    user_id=USER,
                    server_id=server,
                    provider_user_id=identity.user_id.value,
                    link_state="verified",
                )
            )
            db.commit()
            store.apply(db, USER, snapshot, reads.begin_read(db, lambda: datetime.now(UTC)))

        def get(user: int, path: str):
            client.cookies.clear()
            return client.get(path, headers=accounts.cookie(user))

        libraries = {item["name"]: item for item in get(OWNER, "/api/libraries").json()}
        seen = {item["name"]: item for item in get(USER, "/api/libraries").json()}
        movies, family = libraries["Movies"]["id"], libraries["Family"]["id"]
        owner_movies = get(OWNER, f"/api/libraries/{movies}/items").json()
        owner_family = get(OWNER, f"/api/libraries/{family}/items").json()
        casey_family = get(USER, f"/api/libraries/{family}/items").json()
        film = owner_movies["items"][0]["id"]
        refused = {
            "listing": get(USER, f"/api/libraries/{movies}/items"),
            "item": get(USER, f"/api/library-items/{film}"),
            "image": get(USER, f"/api/library-items/{film}/image"),
        }
        opened = get(USER, f"/api/library-items/{casey_family['items'][0]['id']}")
        checks = {
            "caseyLibraries": sorted(seen),
            "caseyLocationsHidden": all(item["locations"] == [] for item in seen.values()),
            "ownerMoviesTotal": owner_movies["total_count"],
            "ownerFamilyTotal": owner_family["total_count"],
            "caseyFamilyTotal": casey_family["total_count"],
            "caseyFamilyPathsHidden": all(i["path"] is None for i in casey_family["items"]),
            "ownerPathsShown": all(i["path"] for i in owner_family["items"]),
            "refused": {
                name: [reply.status_code, reply.json().get("code")]
                for name, reply in refused.items()
            },
            "openedItem": [opened.status_code, opened.json().get("path")],
        }
        checks["ok"] = (
            checks["caseyLibraries"] == ["Family", "Home"]
            and checks["caseyLocationsHidden"]
            and owner_movies["total_count"] > 0
            and casey_family["total_count"] == owner_family["total_count"] > 0
            and checks["caseyFamilyPathsHidden"]
            and checks["ownerPathsShown"]
            and all(value == [404, "not_found"] for value in checks["refused"].values())
            and checks["openedItem"] == [200, None]
        )
        return {"catalog": checks}
    finally:
        client.close()
        accounts.close()
        patch.close()
        subprocess.run(["rm", "-rf", str(scratch)], check=False)


def run() -> int:
    try:
        docker("version", "--format", "{{.Server.Version}}")
        docker("image", "inspect", IMAGE)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"BLOCKED: Docker and the pinned image are required ({IMAGE}): {error}")
        return 2
    name = "catalog-" + secrets.token_hex(4)
    evidence, scratch = ROOT / name, ROOT / f"{name}.scratch"
    evidence.mkdir(parents=True)
    scratch.mkdir()
    (scratch / MARKER).write_text(name)
    instance = {
        "run": name,
        "image": IMAGE,
        "container": name,
        "containerId": None,
        "volumes": [f"{name}-cache"],
        "volumeCreatedAt": {},
        "volumesBefore": docker("volume", "ls", "-q").stdout.split(),
        "scratch": str(scratch),
        "checkout": str(CHECKOUT),
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    save(evidence, "instance.json", instance)
    print(f"Evidence: {evidence}", flush=True)
    secrets_used: list[str] = []
    failures: list[str] = []
    try:
        base = live.launch(instance, evidence)
        live.doctor(instance, base, evidence)
        admin, key, casey, secrets_used = live.setup(base)
        admin.call("POST", "/Library/Refresh")
        live.wait_indexed(base, key, admin)
        root.LIBRARY_ITEMS_CACHE.clear()
        root.JELLYFIN_ITEM_CACHE.clear()
        results = journey(base, key, casey, Path(instance["scratch"]) / "media")
        failures = [n for n, v in results.items() if isinstance(v, dict) and not v.get("ok")]
        save(evidence, "results.json", results)
    except Exception as error:  # noqa: BLE001
        failures.append(f"{type(error).__name__}: {error}")
        save(evidence, "failure.json", {"error": failures[-1], "trace": traceback.format_exc()})
    finally:
        left = cleanup(instance, evidence)
        clean_text = live.redaction(evidence, secrets_used)
    print(json.dumps({"failures": failures, "cleanup": left, "redacted": clean_text}, indent=1))
    return 1 if failures or not left["clean"] or not clean_text else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["run"]:
        sys.exit(run())
    print(__doc__, file=sys.stderr)
    sys.exit(64)
