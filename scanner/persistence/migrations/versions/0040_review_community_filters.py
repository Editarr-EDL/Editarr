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
a filter with no remaining range stays only in the snapshot. Each snapshot entry
records the enabled state its filter started with. No other row changes. The
adoption path runs this only after its verified, recoverable backup.
"""

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
    "manually_added, manually_edited, community_source_id, community_filter_id) "
    "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, "
    "NULL, NULL, ?, ?)"
)


def _classification(entry):
    category, subcategory = entry.get("category"), entry.get("subcategory")
    if category is not None and _FAMILY_OF.get(subcategory) == category:
        return category, subcategory
    return None, None


def _severity(value):
    return value if value in ("low", "medium", "high") else None


def _confidence(value):
    return value if isinstance(value, (int, float)) and 0 <= value <= 1 else None


def _materialize(bind):
    """Give every stored snapshot filter a review filter, enabled when exports used it."""
    sources = bind.exec_driver_sql(
        "SELECT review_id, id, filters_json FROM review_community_sources "
        "ORDER BY review_id, position, id"
    ).fetchall()
    for review_id, source_id, encoded in sources:
        position = bind.exec_driver_sql(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM review_filters WHERE review_id = ?",
            (review_id,),
        ).scalar()
        entries = json.loads(encoded)
        for entry in entries:
            category, subcategory = _classification(entry)
            entry["default_selected"] = category is not None
            start_ms, end_ms = max(0, int(entry["start_ms"])), int(entry["end_ms"])
            if start_ms != entry["start_ms"]:
                entry["initial"] = {"start_ms": start_ms, "end_ms": end_ms}
            if start_ms >= end_ms:
                entry["outside"] = True
                continue
            mute = entry.get("edit_action") == "mute"
            bind.exec_driver_sql(
                _INSERT,
                (
                    review_id,
                    str(uuid.uuid4()),
                    position,
                    "audio" if mute else "visual",
                    category,
                    subcategory,
                    _severity(entry.get("severity")),
                    "audio" if mute else "both",
                    start_ms / 1000,
                    end_ms / 1000,
                    _confidence(entry.get("confidence")),
                    "mute" if mute else "skip",
                    category is not None,
                    source_id,
                    entry["filter_id"],
                ),
            )
            position += 1
        bind.exec_driver_sql(
            "UPDATE review_community_sources SET filters_json = ? WHERE review_id = ? AND id = ?",
            (json.dumps(entries, sort_keys=True, separators=(",", ":")), review_id, source_id),
        )


def upgrade():
    with op.batch_alter_table("review_filters", recreate="always") as batch:
        batch.alter_column("source_scan_id", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("community_source_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("community_filter_id", sa.String(128), nullable=True))
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
        batch.drop_column("community_filter_id")
        batch.drop_column("community_source_id")
        batch.alter_column("source_scan_id", existing_type=sa.String(), nullable=False)
