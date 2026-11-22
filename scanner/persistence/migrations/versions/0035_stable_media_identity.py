"""Add independent catalog titles and exact or historical file versions."""

import json
from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0035_stable_media_identity"
down_revision = "0034_scan_read_indexes"
branch_labels = None
depends_on = None


def _ref(field, target, *, primary=False, cascade=False):
    return sa.Column(
        field,
        sa.String(),
        sa.ForeignKey(target, ondelete="CASCADE" if cascade else None),
        primary_key=primary,
        nullable=False,
    )


def _create_schema():
    op.create_table(
        "media_titles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("entity_type", sa.String(), nullable=False),
    )
    op.create_table(
        "media_title_sources",
        _ref("media_item_id", "media_items.id", primary=True, cascade=True),
        _ref("title_id", "media_titles.id"),
    )
    op.create_index("ix_media_title_sources_title_id", "media_title_sources", ["title_id"])
    op.create_table(
        "media_title_aliases",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("entity_type", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), primary_key=True),
        _ref("title_id", "media_titles.id"),
    )
    op.create_index("ix_media_title_aliases_title_id", "media_title_aliases", ["title_id"])
    op.create_table(
        "media_versions",
        sa.Column("id", sa.String(), primary_key=True),
        _ref("title_id", "media_titles.id"),
        sa.Column("content_sha256", sa.String(), nullable=True),
        sa.Column("video_hash", sa.String(), nullable=True),
        sa.UniqueConstraint("title_id", "content_sha256", name="uq_media_versions_title_sha256"),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR (length(content_sha256) = 64 "
            "AND content_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_media_versions_sha256",
        ),
    )
    op.create_index("ix_media_versions_title_id", "media_versions", ["title_id"])
    op.create_table(
        "media_version_locators",
        _ref("version_id", "media_versions.id", primary=True),
        _ref("media_item_id", "media_items.id", primary=True, cascade=True),
        sa.Column("media_source_id", sa.String(), primary_key=True),
        sa.Column("source_path", sa.String(), primary_key=True),
        sa.Column("resolved_path", sa.String(), primary_key=True),
    )
    op.create_index(
        "ix_media_version_locators_media_item_id", "media_version_locators", ["media_item_id"]
    )
    for table, id_field, target in (
        ("scan_media_versions", "scan_id", "scans.id"),
        ("review_media_versions", "review_id", "saved_reviews.id"),
    ):
        op.create_table(
            table,
            _ref(id_field, target, primary=True, cascade=True),
            _ref("version_id", "media_versions.id"),
        )
        op.create_index(f"ix_{table}_version_id", table, ["version_id"])


def _id(namespace, *values):
    return str(uuid5(NAMESPACE_URL, json.dumps(["editarr", namespace, *values])))


def _typed_key(provider, kind, value):
    if (
        provider not in ("tmdb", "tvdb")
        or not isinstance(value, str)
        or len(value) > 64
        or not value.isascii()
        or not value.isalnum()
    ):
        return None
    if value.isdecimal():
        value = value.lstrip("0")
    return (provider, kind, value) if value else None


def _confirmed_aliases(raw, kind):
    try:
        document = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return set()
    if not isinstance(document, dict) or document.get("entity_type") != kind:
        return set()
    aliases = _identifier_keys(document.get("identifiers"), kind)
    if kind == "episode":
        span = _aired_span(document.get("episode_span") or document.get("episode_numbers"))
        if span:
            aliases.update(
                (provider, "aired_episode", f"{series}:{span}")
                for provider, _, series in _identifier_keys(
                    document.get("series_identifiers"), "series"
                )
            )
    return aliases


def _identifier_keys(identifiers, kind):
    if not isinstance(identifiers, list):
        return set()
    return {
        key
        for item in identifiers
        if isinstance(item, dict) and item.get("entity_type") == kind
        if (key := _typed_key(item.get("provider"), kind, item.get("value")))
    }


def _aired_span(numbers):
    if not isinstance(numbers, list) or not numbers:
        return None
    points = set()
    for item in numbers:
        if not isinstance(item, dict) or item.get("scheme") != "aired":
            return None
        season, number = item.get("season"), item.get("number")
        if type(season) is not int or type(number) is not int or season < 0 or number < 0:
            return None
        points.add((season, number))
    return ",".join(f"{season}.{number}" for season, number in sorted(points))


def _conflict(aliases):
    return len(aliases) != len({(provider, kind) for provider, kind, _ in aliases})


def _backfill_titles(db):
    scan_aliases = defaultdict(set)
    for item_id, hint in db.exec_driver_sql(
        "SELECT DISTINCT s.media_item_id, s.media_provider_id FROM scans AS s "
        "JOIN media_items AS m ON m.id = s.media_item_id "
        "WHERE m.archived_source IS NULL AND lower(m.item_type) = 'movie' "
        "AND s.media_provider_id LIKE 'tmdb:%'"
    ):
        key = _typed_key("tmdb", "movie", hint.removeprefix("tmdb:"))
        if key is not None:
            scan_aliases[item_id].add(key)
    titles = {}
    keys_by_title = defaultdict(set)
    claims = {}
    for item in db.exec_driver_sql(
        "SELECT id, lower(item_type) AS kind, confirmed_identity_json FROM media_items "
        "WHERE archived_source IS NULL ORDER BY created_at, id"
    ):
        kind = item.kind
        aliases = _confirmed_aliases(item.confirmed_identity_json, kind) | scan_aliases[item.id]
        candidates = {claims[key] for key in aliases if key in claims}
        title = next(iter(candidates)) if len(candidates) == 1 else None
        conflict = len(candidates) > 1 or _conflict(aliases | keys_by_title.get(title, set()))
        if title is None or conflict:
            title = _id("title", item.id)
            db.exec_driver_sql("INSERT INTO media_titles VALUES (?, ?)", (title, kind))
        db.exec_driver_sql("INSERT INTO media_title_sources VALUES (?, ?)", (item.id, title))
        titles[item.id] = title
        if conflict:
            continue
        for key in sorted(aliases - claims.keys()):
            db.exec_driver_sql("INSERT INTO media_title_aliases VALUES (?, ?, ?, ?)", (*key, title))
            claims[key] = title
        keys_by_title[title].update(aliases)
    return titles


def _backfill_versions(db, titles):
    scan_versions = {}
    for scan in db.exec_driver_sql(
        "SELECT id, media_item_id, COALESCE(media_source_id, '') AS source_id, "
        "source_path, resolved_local_path, video_hash FROM scans "
        "WHERE status IN ('complete', 'failed', 'cancelled') ORDER BY id"
    ):
        title = titles.get(scan.media_item_id)
        if title is None:
            continue
        locator = (scan.media_item_id, scan.source_id, scan.source_path, scan.resolved_local_path)
        version = (
            _id("legacy-file", *locator, scan.video_hash)
            if scan.video_hash and scan.resolved_local_path
            else _id("legacy-scan", scan.id)
        )
        db.exec_driver_sql(
            "INSERT OR IGNORE INTO media_versions (id, title_id, video_hash) VALUES (?, ?, ?)",
            (version, title, scan.video_hash),
        )
        db.exec_driver_sql(
            "INSERT OR IGNORE INTO media_version_locators VALUES (?, ?, ?, ?, ?)",
            (version, *locator),
        )
        db.exec_driver_sql("INSERT INTO scan_media_versions VALUES (?, ?)", (scan.id, version))
        scan_versions[scan.id] = (scan.media_item_id, scan.source_id, version)
    return scan_versions


def _backfill_reviews(db, titles, scan_versions):
    memberships = defaultdict(list)
    for review, scan in db.exec_driver_sql(
        "SELECT review_id, scan_id FROM review_scan_memberships"
    ):
        memberships[review].append(scan_versions.get(scan))
    for review in db.exec_driver_sql(
        "SELECT id, media_item_id, media_source_id FROM saved_reviews ORDER BY id"
    ):
        title = titles.get(review.media_item_id)
        if title is None:
            continue
        members = set(memberships[review.id])
        media = (review.media_item_id, review.media_source_id)
        match = next(iter(members)) if len(members) == 1 else None
        if match is not None and match[:2] == media:
            version = match[2]
        else:
            version = _id("legacy-review", review.id)
            db.exec_driver_sql(
                "INSERT INTO media_versions (id, title_id) VALUES (?, ?)",
                (
                    version,
                    title,
                ),
            )
            db.exec_driver_sql(
                "INSERT INTO media_version_locators VALUES (?, ?, ?, '', '')", (version, *media)
            )
        db.exec_driver_sql("INSERT INTO review_media_versions VALUES (?, ?)", (review.id, version))


def upgrade():
    _create_schema()
    db = op.get_bind()
    titles = _backfill_titles(db)
    _backfill_reviews(db, titles, _backfill_versions(db, titles))


def downgrade():
    for table in (
        "review_media_versions",
        "scan_media_versions",
        "media_version_locators",
        "media_versions",
        "media_title_aliases",
        "media_title_sources",
        "media_titles",
    ):
        op.drop_table(table)
