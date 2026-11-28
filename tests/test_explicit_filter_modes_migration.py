"""0031 gives every account its own filter mode without changing what it sees or what is required.

Accounts at 0030: 2 chose live with a severity override, 3 chose copy and has its own
copy, 4 follows the default with a rule and a severity required and an override
stored beneath them, and 1 and 5 have no preference row. Each case runs with each
instance default mode.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.models import FilterDefaultsUpdate, FilterSelectionRule, McfCategory, Severity
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from scanner.persistence.repository.filter_preferences import (
    effective_filter_settings,
    enforced_filter_policy,
    filter_preferences_view,
)
from scanner.persistence.repository.repository_settings import replace_filter_defaults
from scanner.settings.filter_defaults import EnforcedFilterPolicy

SOURCE = "0030_remove_passwords"
_TIME = "2026-11-01 00:00:00.000000"
REQUIRED = FilterSelectionRule(
    id="11111111-1111-4111-8111-111111111111",
    disposition="deny",
    category="language",
    severity="high",
)
OTHER = FilterSelectionRule(
    id="22222222-2222-4222-8222-222222222222", disposition="allow", category="sex"
)
_RULE_COLUMNS = ("disposition", "category", "severity")
_FOLLOWERS = (1, 4, 5)


def _rule_values(rule):
    return tuple(getattr(rule, name) for name in _RULE_COLUMNS)


def _seed(path, default_mode):
    with sqlite3.connect(path) as database:
        database.execute("UPDATE filter_settings SET default_mode = ?", (default_mode,))
        database.execute(
            "UPDATE mcf_severity_defaults SET severity = 'high' WHERE subcategory = 'swearing'"
        )
        database.executemany(
            "INSERT INTO filter_selection_rules (id, settings_id, position, disposition, "
            "category, severity) VALUES (?, 1, ?, ?, ?, ?)",
            [(REQUIRED.id, 4, *_rule_values(REQUIRED)), (OTHER.id, 9, *_rule_values(OTHER))],
        )
        database.executemany(
            "INSERT INTO users (id, role, status, local_export_id, permission_revision, "
            "created_at, updated_at) VALUES (?, ?, 'active', ?, 1, ?, ?)",
            [
                (
                    user_id,
                    "owner" if user_id == 1 else "user",
                    f"local-{user_id:032x}",
                    _TIME,
                    _TIME,
                )
                for user_id in range(1, 6)
            ],
        )
        database.executemany(
            "INSERT INTO user_filter_preferences VALUES (?, ?, ?, ?, ?)",
            [(2, "live", 1, 0, _TIME), (3, "copy", 2, 0, _TIME), (4, None, 5, 3, _TIME)],
        )
        database.execute("INSERT INTO user_filter_severity_overrides VALUES (2, 'swearing', 'low')")
        database.execute(
            "INSERT INTO user_filter_copy_rules (user_id, id, position, disposition, category) "
            "VALUES (3, ?, 0, 'allow', 'sex')",
            (OTHER.id,),
        )
        database.executemany(
            "INSERT INTO user_filter_copy_severities VALUES (3, ?, ?, 'low')",
            database.execute(
                "SELECT subcategory, position FROM mcf_severity_defaults ORDER BY position"
            ).fetchall(),
        )
        database.executemany(
            "INSERT INTO user_filter_enforcements VALUES (4, ?, ?)",
            [("selection_rule", REQUIRED.id), ("severity", "swearing")],
        )
        database.execute(
            "INSERT INTO user_filter_rule_overrides (user_id, rule_id, disabled) VALUES (4, ?, 1)",
            (REQUIRED.id,),
        )


def _rows(path, table):
    with sqlite3.connect(path) as database:
        return database.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()


def _built(tmp_path, default_mode):
    path = tmp_path / "scanner.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            upgrade_connection(connection, SOURCE)
    finally:
        engine.dispose()
    _seed(path, default_mode)
    return path


@pytest.mark.parametrize("default_mode", ["live", "copy"])
def test_followers_take_the_default_mode_and_keep_what_they_see_and_what_is_required(
    tmp_path, default_mode
):
    path = _built(tmp_path, default_mode)
    kept = {
        table: _rows(path, table)
        for table in (
            "user_filter_enforcements",
            "user_filter_rule_overrides",
            "user_filter_severity_overrides",
        )
    }

    assert adoption.upgrade_existing_database(path, path.parent).is_dir()

    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert {table: _rows(path, table) for table in kept} == kept
    preferences = [row[:4] for row in _rows(path, "user_filter_preferences")]
    assert preferences == [
        (1, default_mode, 0, 0),
        (2, "live", 1, 0),
        (3, "copy", 2, 0),
        (4, default_mode, 5, 3),
        (5, default_mode, 0, 0),
    ]
    copied = _FOLLOWERS if default_mode == "copy" else ()
    copy_rules = [(*row[:4], row[5], row[8]) for row in _rows(path, "user_filter_copy_rules")]
    assert copy_rules == sorted(
        [(3, OTHER.id, 0, "allow", "sex", None)]
        + [
            (user_id, rule.id, position, *_rule_values(rule))
            for user_id in copied
            for position, rule in enumerate((REQUIRED, OTHER))
        ]
    )
    severities = _rows(path, "user_filter_copy_severities")
    assert {user_id for user_id, *_ in severities} == {3, *copied}
    with sqlite3.connect(path) as database:
        instance = database.execute(
            "SELECT subcategory, severity FROM mcf_severity_defaults ORDER BY position"
        ).fetchall()
    for user_id in copied:
        copy = sorted(
            (position, subcategory, severity)
            for owner, subcategory, position, severity in severities
            if owner == user_id
        )
        assert copy == [(position, *item) for position, item in enumerate(instance)]


def _enforced(view):
    rules = [entry.rule for entry in view.rules if entry.enforced]
    return rules, {entry.subcategory: entry.severity for entry in view.severities if entry.enforced}


@pytest.mark.parametrize("default_mode", ["live", "copy"])
def test_the_upgrade_keeps_each_view_and_never_shadows_a_required_item(tmp_path, default_mode):
    path = _built(tmp_path, default_mode)
    adoption.upgrade_existing_database(path, path.parent)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            assert enforced_filter_policy(db, 4) == EnforcedFilterPolicy(
                3, (REQUIRED,), {McfCategory.swearing: Severity.HIGH}
            )
            for user_id in _FOLLOWERS:
                settings = effective_filter_settings(db, user_id)
                assert settings.selection_rules == [REQUIRED, OTHER]
            assert _enforced(filter_preferences_view(db, 4)) == (
                [REQUIRED],
                {McfCategory.swearing: Severity.HIGH},
            )
            live = effective_filter_settings(db, 2).severity_defaults
            assert {item.subcategory: item.severity for item in live}["swearing"] == "low"
            assert effective_filter_settings(db, 3).selection_rules == [OTHER]

            edited = REQUIRED.model_copy(update={"severity": Severity.MEDIUM})
            update = FilterDefaultsUpdate(
                revision=filter_preferences_view(db, 2).instance_revision,
                selection_rules=[edited, OTHER],
                severity_defaults=[
                    {"subcategory": item, "severity": "low"} for item in McfCategory
                ],
            )
            replace_filter_defaults(db, update)

            assert enforced_filter_policy(db, 4) == EnforcedFilterPolicy(
                4, (edited,), {McfCategory.swearing: Severity.LOW}
            )
            assert _enforced(filter_preferences_view(db, 4)) == (
                [edited],
                {McfCategory.swearing: Severity.LOW},
            )
            follower = effective_filter_settings(db, 5).selection_rules
            assert follower == ([edited, OTHER] if default_mode == "live" else [REQUIRED, OTHER])
    finally:
        engine.dispose()
