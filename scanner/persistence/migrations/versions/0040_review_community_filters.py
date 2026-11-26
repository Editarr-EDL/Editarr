"""Hold community filters as normal saved-review filters.

A saved review's included community snapshots (0039) now also contribute ordinary
`review_filters` rows, which the user enables, disables and adjusts like scan
filters. `review_filters` is rebuilt so a row comes from a scan (`source_scan_id`) or
from a community snapshot (`community_source_id`, `community_filter_id`):

- `source_scan_id` becomes nullable; a check keeps exactly one provenance kind.
- `(review_id, community_source_id)` references `review_community_sources` and
  cascades with it; a snapshot filter appears at most once per review.

Every existing snapshot keeps its filters: each stored filter becomes an enabled
review filter after the review's existing filters, so exports keep their content. A
filter whose subcategory doesn't belong to its category, which exports already left
out, becomes an unclassified, disabled filter; a negative start is clipped to 0, and
a filter with no remaining range stays only in the snapshot.

The snapshot's entries become the link's master values of the snapshot's revision
(its kept verified snapshot, else the link's rows), with the owner's threshold and
Low Confidence preference deciding each filter's starting state, and the digest is
recomputed for them. Measured against them, a stored filter the owner adjusted counts
as edited by the user, and keeps its enabled state; turning a filter on or off is never
an edit. A filter the owner had rejected becomes a disabled, unedited filter, like the
master's other filters the snapshot left out, which are added disabled. A snapshot
without a link keeps its stored filters as their own defaults. Community-tab decisions
of links that no review includes no longer apply; their rows are kept. A database
built by the earlier published 0039 has
`community_master_preferences.hide_rejected_downvoted`; it is renamed to
`hide_downvoted`, keeping each account's value. No other row changes. The adoption
path runs this only after its verified, recoverable backup.
"""

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0040_review_community_filters"
down_revision = "0039_review_community_sources"
branch_labels = None
depends_on = None

# The MCF taxonomy as of this revision: each family's subcategory tokens.
_TAXONOMY = {
    "commercial_content": "commercial advertBreak consumerism productPlacement",
    "discrimination": (
        "discrimination adultism antisemitism genderism homophobia misandry "
        "misogyny racism sexism supremacism transphobia xenophobia"
    ),
    "dispensable_scenes": "dispensable idiocy tedious",
    "drugs": (
        "drugs alcohol antipsychotics cigarettes depressants gambling hallucinogens stimulants"
    ),
    "fear": (
        "fear accident acrophobia aliens arachnophobia astraphobia aviophobia "
        "chemophobia claustrophobia coulrophobia cynophobia death dentophobia "
        "emetophobia enochlophobia explosion fire gerascophobia ghosts grave "
        "hemophobia hylophobia melissophobia misophonia musophobia mysophobia "
        "nosocomephobia nyctophobia siderodromophobia thalassophobia vampires"
    ),
    "language": "language blasphemy nameCalling sexualDialogue swearing vulgarity",
    "nudity": "nudity bareButtocks exposedGenitalia fullNudity toplessness",
    "sex": (
        "sex adultery analSex coitus kissing masturbation objectification "
        "oralSex premaritalSex promiscuity prostitution"
    ),
    "violence": (
        "violence choking crueltyToAnimals culturalViolence desecration "
        "emotionalViolence kicking massacre murder punching rape slapping "
        "slavery stabbing torture warfare weapons"
    ),
}
_FAMILY_OF = {token: family for family, tokens in _TAXONOMY.items() for token in tokens.split()}
_PROVENANCE = (
    "(source_scan_id IS NULL) = (community_source_id IS NOT NULL) "
    "AND (community_source_id IS NULL) = (community_filter_id IS NULL) "
    "AND (community_source_id IS NULL OR source_finding_id IS NULL)"
)
_INSERT = (
    "INSERT INTO review_filters (review_id, id, source_scan_id, source_finding_id, position, "
    "source, category, subcategory, severity, mcf_channel, mcf_comment, label, details, "
    "group_key, start_seconds, end_seconds, confidence, edit_action, selected, "
    "manually_added, manually_edited, community_source_id, community_filter_id, "
    "community_edited) "
    "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, "
    "NULL, NULL, ?, ?, ?)"
)
# Master values that change a filter, as the snapshot digest of this revision reads them.
_MATERIAL = ("category", "subcategory", "severity", "edit_action", "start_ms", "end_ms")


def _classification(entry):
    category, subcategory = entry.get("category"), entry.get("subcategory")
    if category is not None and _FAMILY_OF.get(subcategory) == category:
        return category, subcategory
    return None, None


def _severity(value):
    return value if value in ("low", "medium", "high") else None


def _confidence(value):
    return value if isinstance(value, (int, float)) and 0 <= value <= 1 else None


def _digest(entries):
    document = [
        [entry["filter_id"], *(entry.get(name) for name in _MATERIAL)]
        for entry in entries
        if entry.get("served", True) is not False
    ]
    encoded = json.dumps(sorted(document), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aligned(value, alignment):
    if not alignment:
        return int(value)
    return int(round(value * float(alignment.get("scale", 1.0)))) + int(
        alignment.get("offsetMs", 0)
    )


def _master_entry(item, alignment, info, preferences):
    """A snapshot entry from a master filter's raw values and informational fields."""
    threshold, show_low = preferences
    low = bool(item.get("lowConfidence"))
    confidence = info.get("confidence")
    return {
        "filter_id": item["filterId"],
        "category": item["category"],
        "subcategory": item.get("subcategory"),
        "severity": item.get("severity"),
        "edit_action": item.get("action"),
        "start_ms": _aligned(item["startMs"], alignment),
        "end_ms": _aligned(item["endMs"], alignment),
        "low_confidence": low,
        "confidence": confidence,
        "contributor_count": info.get("contributorCount"),
        "default_selected": (not low or show_low) and (confidence or 0.0) >= threshold,
    }


def _masters(bind, link_id, revision, alignment, preferences):
    """The link's master filters as of `revision`.

    The verified snapshot of that revision is used when kept; otherwise the link's
    present rows (their last applied master values).
    """
    if link_id is None:
        return {}
    rows = bind.exec_driver_sql(
        "SELECT filter_id, category, baseline_json, info_json, present "
        "FROM community_master_filters WHERE link_id = ?",
        (link_id,),
    ).fetchall()
    stored = bind.exec_driver_sql(
        "SELECT snapshot_bytes FROM community_master_snapshots WHERE link_id = ? AND revision = ?",
        (link_id, revision),
    ).scalar()
    masters = {}
    try:
        for item in json.loads(stored)["filters"] if stored else ():
            masters[item["filterId"]] = _master_entry(item, alignment, item, preferences)
    except (ValueError, KeyError, TypeError):
        masters = {}
    if not masters:
        for filter_id, category, baseline, info, present in rows:
            if present:
                item = {**json.loads(baseline), "filterId": filter_id, "category": category}
                masters[filter_id] = _master_entry(item, alignment, json.loads(info), preferences)
    return masters


def _differs(entry, values):
    """Whether stored values differ from an entry's defaults; the enabled state never counts."""
    category, subcategory = _classification(entry)
    return (
        (category, subcategory) != _classification(values)
        or _severity(values.get("severity")) != _severity(entry.get("severity"))
        or (values.get("edit_action") == "mute") != (entry.get("edit_action") == "mute")
        or max(0, int(values["start_ms"])) != max(0, int(entry["start_ms"]))
        or int(values["end_ms"]) != int(entry["end_ms"])
    )


def _source_rows(bind, review_id, source):
    """Snapshot entries and review filters for one stored 0039 snapshot.

    Its filters were the link's active filters with the owner's decisions applied. With
    the link's master values as the defaults, an adjusted filter counts as edited, and a
    rejected one, like other filters the snapshot left out, is added disabled and
    unedited. Without master values the stored filters are their own defaults.
    """
    source_id, link_id, revision, alignment_json, encoded = source
    legacy = {entry["filter_id"]: entry for entry in json.loads(encoded)}
    owner = bind.exec_driver_sql(
        "SELECT owner_id FROM saved_reviews WHERE id = ?", (review_id,)
    ).scalar()
    preferences = bind.exec_driver_sql(
        "SELECT confidence_threshold, show_low_confidence FROM community_master_preferences "
        "WHERE owner_id = ?",
        (owner,),
    ).first() or (0.0, False)
    alignment = json.loads(alignment_json) if alignment_json else None
    masters = _masters(bind, link_id, revision, alignment, preferences)
    entries, rows = [], []
    for filter_id in sorted(set(masters) | set(legacy)):
        entry = masters.get(filter_id)
        stored = legacy.get(filter_id)
        if entry is None:
            entry = {**stored, "default_selected": _classification(stored)[0] is not None}
            values, selected, edited = stored, entry["default_selected"], False
        elif stored is not None:
            selected = _classification(stored)[0] is not None
            values, edited = stored, _differs(entry, stored)
        else:
            values, selected, edited = entry, False, False
        entries.append(entry)
        rows.append((filter_id, values, selected, edited))
    return entries, rows


def _materialize(bind):
    """Give every stored snapshot filter a review filter, enabled when exports used it."""
    sources = bind.exec_driver_sql(
        "SELECT review_id, id, link_id, master_revision, alignment_json, filters_json "
        "FROM review_community_sources ORDER BY review_id, position, id"
    ).fetchall()
    for review_id, *source in sources:
        source_id = source[0]
        position = bind.exec_driver_sql(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM review_filters WHERE review_id = ?",
            (review_id,),
        ).scalar()
        entries, rows = _source_rows(bind, review_id, source)
        by_id = {entry["filter_id"]: entry for entry in entries}
        for filter_id, values, selected, edited in sorted(
            rows, key=lambda row: (row[1]["start_ms"], row[1]["end_ms"], row[0])
        ):
            entry = by_id[filter_id]
            category, subcategory = _classification(values)
            start_ms, end_ms = max(0, int(values["start_ms"])), int(values["end_ms"])
            if start_ms >= end_ms:
                entry["outside"] = True
                continue
            if not edited and (start_ms, end_ms) != (entry["start_ms"], entry["end_ms"]):
                entry["initial"] = {"start_ms": start_ms, "end_ms": end_ms}
            mute = values.get("edit_action") == "mute"
            bind.exec_driver_sql(
                _INSERT,
                (
                    review_id,
                    str(uuid.uuid4()),
                    position,
                    "audio" if mute else "visual",
                    category,
                    subcategory,
                    _severity(values.get("severity")),
                    "audio" if mute else "both",
                    start_ms / 1000,
                    end_ms / 1000,
                    _confidence(values.get("confidence")),
                    "mute" if mute else "skip",
                    selected,
                    source_id,
                    filter_id,
                    edited,
                ),
            )
            position += 1
        bind.exec_driver_sql(
            "UPDATE review_community_sources SET filters_json = ?, filters_digest = ? "
            "WHERE review_id = ? AND id = ?",
            (
                json.dumps(entries, sort_keys=True, separators=(",", ":")),
                _digest(entries),
                review_id,
                source_id,
            ),
        )


def _rename_earlier_preference(bind):
    """Rename the column an earlier published 0039 named `hide_rejected_downvoted`."""
    columns = {
        row[1] for row in bind.exec_driver_sql("PRAGMA table_info(community_master_preferences)")
    }
    if "hide_rejected_downvoted" in columns:
        bind.exec_driver_sql(
            "ALTER TABLE community_master_preferences "
            "RENAME COLUMN hide_rejected_downvoted TO hide_downvoted"
        )


def upgrade():
    _rename_earlier_preference(op.get_bind())
    with op.batch_alter_table("review_filters", recreate="always") as batch:
        batch.alter_column("source_scan_id", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("community_source_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("community_filter_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("community_edited", sa.Boolean(), nullable=True))
        batch.create_foreign_key(
            "fk_review_filters_community_source",
            "review_community_sources",
            ["review_id", "community_source_id"],
            ["review_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_review_filters_community_filter",
            ["review_id", "community_source_id", "community_filter_id"],
        )
        batch.create_check_constraint("ck_review_filters_provenance", _PROVENANCE)
    _materialize(op.get_bind())


def downgrade():
    """Undo only when nothing would be lost: no community review filter exists."""
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT 1 FROM review_filters WHERE community_source_id IS NOT NULL LIMIT 1"
    ).first():
        raise RuntimeError("Restore a backup to downgrade without discarding review filters")
    with op.batch_alter_table("review_filters", recreate="always") as batch:
        batch.drop_constraint("ck_review_filters_provenance", type_="check")
        batch.drop_constraint("uq_review_filters_community_filter", type_="unique")
        batch.drop_constraint("fk_review_filters_community_source", type_="foreignkey")
        batch.drop_column("community_edited")
        batch.drop_column("community_filter_id")
        batch.drop_column("community_source_id")
        batch.alter_column("source_scan_id", existing_type=sa.String(), nullable=False)
