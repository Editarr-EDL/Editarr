"""A freshly migrated database with two accounts, one local version and a model finding,
plus builders for verified master snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from scanner.db import (
    MediaItemRow,
    MediaTitleRow,
    MediaVersionRow,
    ScanFindingRow,
    ScanMediaVersionRow,
    ScanRow,
    UserRow,
    initialize_sqlite_connection,
)
from scanner.formats.export_layout import new_local_export_id
from scanner.modeling.community_master_diff import MasterFilter, MasterValues
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.community_master_links import (
    LinkVersion,
    VerifiedSnapshot,
    apply_master,
)

SERVICE = "https://community.example"
VERSION_ID = "mv_local"
OWNER, MEMBER = 1, 2
T0 = datetime(2026, 11, 1, 12, 0)
INFO = {"confidence": 0.5, "contributorCount": 3, "spread": {"startMs": 0, "endMs": 0}}


def master_database(tmp_path):
    """Return a session factory over a migrated database with foreign keys enforced."""
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", initialize_sqlite_connection)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for user_id, role in ((OWNER, "owner"), (MEMBER, "user")):
            db.add(
                UserRow(
                    id=user_id,
                    role=role,
                    status="active",
                    username=f"user{user_id}",
                    normalized_username=f"user{user_id}",
                    local_export_id=new_local_export_id().value,
                )
            )
        db.add(
            MediaItemRow(
                id="item", server_id="s" * 32, jellyfin_id="j" * 32, item_type="Movie", name="M"
            )
        )
        db.add(MediaTitleRow(id="title", entity_type="movie"))
        db.flush()
        db.add(MediaVersionRow(id=VERSION_ID, title_id="title"))
        db.add(
            ScanRow(
                id="scan",
                media_item_id="item",
                scan_number=1,
                source_path="/m.mkv",
                resolved_local_path="/m.mkv",
                status="complete",
            )
        )
        db.flush()
        db.add(ScanMediaVersionRow(scan_id="scan", version_id=VERSION_ID))
        db.add(
            ScanFindingRow(
                scan_id="scan",
                category="violence",
                subcategory="punching",
                start_seconds=100.0,
                end_seconds=101.0,
                source="video",
            )
        )
        db.commit()
    return engine, factory


def values(start=10_000, end=20_000, **changes) -> MasterValues:
    base = dict(subcategory="punching", severity="high", action="skip")
    return MasterValues(start, end, **{**base, **changes})


def served(filter_id, master=None, *, info=None, merged=(), category="violence"):
    return MasterFilter(filter_id, category, master or values(), info or INFO, tuple(merged))


def snapshot(revision, *filters, version="v_1") -> VerifiedSnapshot:
    document = {
        "versionId": version,
        "revision": revision,
        "filters": [
            {
                "filterId": item.filter_id,
                "mergedIds": list(item.merged_ids),
                "category": item.category,
                **item.values.as_json(),
                **dict(item.info),
            }
            for item in filters
        ],
    }
    raw = json.dumps(document, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return VerifiedSnapshot(
        revision=revision,
        snapshot_digest=digest,
        snapshot_bytes=raw,
        signature={"revision": revision, "snapshotDigest": digest},
        service_key_id="sha256:" + "a" * 64,
        receipt={"receipt": {"receiptId": f"{revision:064d}"}},
        receipt_id=f"{revision:064d}",
        filters=tuple(filters),
    )


def link(db, *filters, revision=1, tier="2", owner=OWNER, version="v_1", replace=False):
    """Apply a master set; tiers 1-3 need no alignment."""
    return apply_master(
        db,
        owner_id=owner,
        media_version_id=VERSION_ID,
        service_url=SERVICE,
        contributor_id="c_1",
        version=LinkVersion(version, "confirmed", "Theatrical", "theatrical", "w_1", tier, 80),
        snapshot=snapshot(revision, *filters, version=version),
        alignment_required=tier not in ("1", "2", "3"),
        replace=replace,
        now=T0,
    )
