"""Store live-mode rule overrides and personal selection rules.

Adds two empty tables owned by `user_filter_preferences`; no existing row changes.
An override switches off or edits one inherited instance rule by ID. Personal rules
repeat the instance rule shape in the user's order.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_personal_filter_rules"
down_revision = "0015_sealed_api_keys"
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
_MATCHERS = (
    "evidence_source IS NOT NULL OR category IS NOT NULL OR "
    "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
    "severity IS NOT NULL OR mcf_channel IS NOT NULL"
)


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def _owner():
    return sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("user_filter_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )


def _rule_fields(nullable_disposition):
    return [
        sa.Column("disposition", sa.String(), nullable=nullable_disposition),
        sa.Column("evidence_source", sa.String(), nullable=True),
        sa.Column("category", sa.String(18), nullable=True),
        sa.Column("subcategory", sa.String(), nullable=True),
        sa.Column("label_contains", sa.String(), nullable=True),
        sa.Column("severity", sa.String(6), nullable=True),
        sa.Column("mcf_channel", sa.String(5), nullable=True),
    ]


def _rule_checks(table):
    """The source, channel, family pair, and enum checks shared with `filter_selection_rules`."""
    families = " OR ".join(
        f"(category = '{family}' AND subcategory IN ({_quoted(tokens.split())}))"
        for family, tokens in _FAMILIES.items()
    )
    return [
        sa.CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name=f"ck_{table}_source",
        ),
        sa.CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name=f"ck_{table}_channel",
        ),
        sa.CheckConstraint(
            f"subcategory IS NULL OR (category IS NOT NULL AND ({families}))",
            name=f"ck_{table}_subcategory_family",
        ),
        sa.CheckConstraint(f"category IN ({_quoted(_FAMILIES)})", name="mcfcategoryfamily"),
        sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name="severity"),
        sa.CheckConstraint("mcf_channel IN ('both', 'video', 'audio')", name="mcfchannel"),
    ]


def upgrade():
    op.create_table(
        "user_filter_rule_overrides",
        _owner(),
        sa.Column("rule_id", sa.String(), primary_key=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        *_rule_fields(True),
        sa.CheckConstraint(
            "disabled = 1 OR disposition IS NOT NULL", name="ck_user_filter_rule_overrides_change"
        ),
        sa.CheckConstraint(
            "disposition IS NULL OR disposition IN ('allow', 'deny')",
            name="ck_user_filter_rule_overrides_disposition",
        ),
        sa.CheckConstraint(
            "disposition IS NOT NULL OR (evidence_source IS NULL AND category IS NULL AND "
            "subcategory IS NULL AND label_contains IS NULL AND severity IS NULL AND "
            "mcf_channel IS NULL)",
            name="ck_user_filter_rule_overrides_fields",
        ),
        sa.CheckConstraint(
            f"disposition IS NULL OR {_MATCHERS}",
            name="ck_user_filter_rule_overrides_has_matcher",
        ),
        *_rule_checks("user_filter_rule_overrides"),
    )
    op.create_table(
        "user_filter_personal_rules",
        _owner(),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_rule_fields(False),
        sa.UniqueConstraint("user_id", "position", name="uq_user_filter_personal_rules_position"),
        sa.CheckConstraint("position >= 0", name="ck_user_filter_personal_rules_position"),
        sa.CheckConstraint(
            "disposition IN ('allow', 'deny')", name="ck_user_filter_personal_rules_disposition"
        ),
        sa.CheckConstraint(_MATCHERS, name="ck_user_filter_personal_rules_has_matcher"),
        *_rule_checks("user_filter_personal_rules"),
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard personal filter rules")
