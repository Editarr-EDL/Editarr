"""Extend the MCF taxonomy and accept 1.1.1 scan documents without relabeling data."""

from alembic import op

revision = "0041_mcf_1_1_1"
down_revision = "0040_review_community_filters"
branch_labels = None
depends_on = None

_LEGACY = {
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
_EXTENSIONS = {
    "discrimination": ("sizeism", "ableism", "ageism"),
    "dispensable_scenes": ("other",),
    "language": ("innuendo",),
    "sex": ("sexualRape",),
    "violence": ("gore", "abuse"),
}
_NEW_TOKENS = tuple(token for tokens in _EXTENSIONS.values() for token in tokens)
_FINDING_TABLES = ("scan_findings", "review_filters")
_RULE_TABLES = (
    ("filter_selection_rules", "ck_filter_rules_subcategory_family"),
    ("user_filter_copy_rules", "ck_user_filter_copy_rules_subcategory_family"),
    ("user_filter_rule_overrides", "ck_user_filter_rule_overrides_subcategory_family"),
    ("user_filter_personal_rules", "ck_user_filter_personal_rules_subcategory_family"),
)


def _taxonomy(extended):
    return {
        family: tuple(tokens.split()) + (_EXTENSIONS.get(family, ()) if extended else ())
        for family, tokens in _LEGACY.items()
    }


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def _replace_check(table, name, predicate):
    with op.batch_alter_table(table, recreate="always") as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, predicate)


def _replace_checks(extended):
    families = " OR ".join(
        f"(category = '{family}' AND subcategory IN ({_quoted(tokens)}))"
        for family, tokens in _taxonomy(extended).items()
    )
    for table in _FINDING_TABLES:
        _replace_check(
            table,
            f"ck_{table}_classification_pair",
            "(category IS NULL AND subcategory IS NULL) OR "
            f"(category IS NOT NULL AND subcategory IS NOT NULL AND ({families}))",
        )
    for table, name in _RULE_TABLES:
        _replace_check(
            table, name, f"subcategory IS NULL OR (category IS NOT NULL AND ({families}))"
        )
    versions = ("1.0.0", "1.1.0", "1.1.1") if extended else ("1.0.0", "1.1.0")
    _replace_check(
        "scan_mcf_documents", "mcfsourceversion", f"source_version IN ({_quoted(versions)})"
    )


def _extend_instance_defaults(bind):
    """Append new defaults without changing the positions or values of legacy defaults."""
    first = bind.exec_driver_sql(
        "SELECT MAX(position) + 1 FROM mcf_severity_defaults WHERE settings_id = 1"
    ).scalar_one()
    bind.exec_driver_sql(
        "INSERT INTO mcf_severity_defaults (settings_id, subcategory, position, severity) "
        "VALUES (1, ?, ?, 'medium')",
        [(token, position) for position, token in enumerate(_NEW_TOKENS, first)],
    )


def _extend_copies(bind):
    """Append default severities to existing copies without changing their saved choices."""
    for token in _NEW_TOKENS:
        bind.exec_driver_sql(
            "INSERT INTO user_filter_copy_severities (user_id, subcategory, position, severity) "
            "SELECT user_id, ?, MAX(position) + 1, 'medium' "
            "FROM user_filter_copy_severities WHERE user_id NOT IN "
            "(SELECT user_id FROM user_filter_copy_severities WHERE subcategory = ?) "
            "GROUP BY user_id",
            (token, token),
        )


def _check_foreign_keys(bind):
    if bind.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError("MCF migration found an invalid foreign key")


def _require_rebuild_connection(bind):
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding MCF tables requires the coordinated migration connection")


def upgrade():
    bind = op.get_bind()
    _require_rebuild_connection(bind)
    _extend_instance_defaults(bind)
    _extend_copies(bind)
    _replace_checks(extended=True)
    _check_foreign_keys(bind)


def downgrade():
    """Revert unused defaults only. New classifications require restoring a backup."""
    bind = op.get_bind()
    _require_rebuild_connection(bind)
    categories = f"subcategory IN ({_quoted(_NEW_TOKENS)})"
    classified = [*_FINDING_TABLES, *(table for table, _name in _RULE_TABLES)]
    checks = [(table, categories) for table in classified]
    checks.extend(
        [
            ("scan_mcf_documents", "source_version = '1.1.1'"),
            ("mcf_severity_defaults", f"{categories} AND severity <> 'medium'"),
            ("user_filter_copy_severities", f"{categories} AND severity <> 'medium'"),
            ("user_filter_severity_overrides", categories),
            (
                "user_filter_enforcements",
                f"kind = 'severity' AND target IN ({_quoted(_NEW_TOKENS)})",
            ),
        ]
    )
    for table, predicate in checks:
        if bind.exec_driver_sql(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1").first():
            raise RuntimeError("Restore a backup to downgrade a database using MCF 1.1.1")
    for table in ("mcf_severity_defaults", "user_filter_copy_severities"):
        bind.exec_driver_sql(f"DELETE FROM {table} WHERE {categories}")
    _replace_checks(extended=False)
    _check_foreign_keys(bind)
