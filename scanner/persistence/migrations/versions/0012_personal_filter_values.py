"""Store copy-mode filter values and live per-user severity overrides.

Adds three empty tables owned by `user_filter_preferences`; no existing row changes.
A copy repeats the instance rule and severity shapes per user. What a live personal
rule means for an ordered instance rule is undecided, so live rules have no table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_personal_filter_values"
down_revision = "0011_activity_ownership"
branch_labels = None
depends_on = None

# The MCF registry at this revision; a later registry change needs its own migration.
_FAMILIES = {
    "commercial_content": "commercial advertBreak consumerism productPlacement",
    "discrimination": (
        "discrimination adultism antisemitism genderism homophobia misandry misogyny racism "
        "sexism supremacism transphobia xenophobia"
    ),
    "dispensable_scenes": "dispensable idiocy tedious",
    "drugs": (
        "drugs alcohol antipsychotics cigarettes depressants gambling hallucinogens stimulants"
    ),
    "fear": (
        "fear accident acrophobia aliens arachnophobia astraphobia aviophobia chemophobia "
        "claustrophobia coulrophobia cynophobia death dentophobia emetophobia enochlophobia "
        "explosion fire gerascophobia ghosts grave hemophobia hylophobia melissophobia "
        "misophonia musophobia mysophobia nosocomephobia nyctophobia siderodromophobia "
        "thalassophobia vampires"
    ),
    "language": "language blasphemy nameCalling sexualDialogue swearing vulgarity",
    "nudity": "nudity bareButtocks exposedGenitalia fullNudity toplessness",
    "sex": (
        "sex adultery analSex coitus kissing masturbation objectification oralSex premaritalSex "
        "promiscuity prostitution"
    ),
    "violence": (
        "violence choking crueltyToAnimals culturalViolence desecration emotionalViolence "
        "kicking massacre murder punching rape slapping slavery stabbing torture warfare weapons"
    ),
}


def _severity():
    return sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name="severity")


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def _pair_check():
    """The `filter_selection_rules` family and subcategory check, for the copy table."""
    families = " OR ".join(
        f"(category = '{family}' AND subcategory IN ({_quoted(tokens.split())}))"
        for family, tokens in _FAMILIES.items()
    )
    return sa.CheckConstraint(
        f"subcategory IS NULL OR (category IS NOT NULL AND ({families}))",
        name="ck_user_filter_copy_rules_subcategory_family",
    )


def _owner():
    return sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("user_filter_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade():
    op.create_table(
        "user_filter_copy_rules",
        _owner(),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(), nullable=False),
        sa.Column("evidence_source", sa.String(), nullable=True),
        sa.Column("category", sa.String(18), nullable=True),
        sa.Column("subcategory", sa.String(), nullable=True),
        sa.Column("label_contains", sa.String(), nullable=True),
        sa.Column("severity", sa.String(6), nullable=True),
        sa.Column("mcf_channel", sa.String(5), nullable=True),
        sa.UniqueConstraint("user_id", "position", name="uq_user_filter_copy_rules_position"),
        sa.CheckConstraint("position >= 0", name="ck_user_filter_copy_rules_position"),
        sa.CheckConstraint(
            "disposition IN ('allow', 'deny')", name="ck_user_filter_copy_rules_disposition"
        ),
        sa.CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name="ck_user_filter_copy_rules_source",
        ),
        sa.CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_user_filter_copy_rules_channel",
        ),
        _pair_check(),
        sa.CheckConstraint(
            "evidence_source IS NOT NULL OR category IS NOT NULL OR "
            "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
            "severity IS NOT NULL OR mcf_channel IS NOT NULL",
            name="ck_user_filter_copy_rules_has_matcher",
        ),
        sa.CheckConstraint(f"category IN ({_quoted(_FAMILIES)})", name="mcfcategoryfamily"),
        _severity(),
        sa.CheckConstraint("mcf_channel IN ('both', 'video', 'audio')", name="mcfchannel"),
    )
    op.create_table(
        "user_filter_copy_severities",
        _owner(),
        sa.Column("subcategory", sa.String(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(6), nullable=False),
        sa.UniqueConstraint("user_id", "position", name="uq_user_filter_copy_severities_position"),
        sa.CheckConstraint("position >= 0", name="ck_user_filter_copy_severities_position"),
        _severity(),
    )
    op.create_table(
        "user_filter_severity_overrides",
        _owner(),
        sa.Column("subcategory", sa.String(), primary_key=True),
        sa.Column("severity", sa.String(6), nullable=False),
        _severity(),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard personal filter values")
