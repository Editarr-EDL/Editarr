"""MU-03: copy-mode rules and severities and live severity overrides are per-user storage."""

import sqlite3

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session

from scanner.backup.archive import export_backup
from scanner.persistence.migrations import adoption
from scanner.persistence.rows.db_policies import (
    UserFilterCopyRuleRow,
    UserFilterCopySeverityRow,
    UserFilterSeverityOverrideRow,
)
from tests.test_activity_ownership import _TIME
from tests.test_activity_ownership import database as database
from tests.test_backup_activation import _fixture, _restore

_RULE_FIELDS = (
    "id",
    "position",
    "disposition",
    "evidence_source",
    "category",
    "subcategory",
    "label_contains",
    "severity",
    "mcf_channel",
)
_RULES = {
    2: [
        ("rule-1", 0, "deny", "audio", "language", "swearing", None, "high", "audio"),
        ("rule-2", 1, "allow", None, None, None, "darn", None, None),
    ],
    3: [("rule-1", 0, "allow", "visual", "violence", "murder", None, None, "video")],
}
_COPY_SEVERITIES = {2: [("swearing", 0, "high"), ("murder", 1, "low")], 3: [("swearing", 0, "low")]}
_OVERRIDES = {2: {"murder": "medium"}, 4: {"swearing": "high"}}


def _write_values(session):
    for user_id, rules in _RULES.items():
        for rule in rules:
            session.add(UserFilterCopyRuleRow(user_id=user_id, **dict(zip(_RULE_FIELDS, rule))))
    for user_id, severities in _COPY_SEVERITIES.items():
        for subcategory, position, severity in severities:
            session.add(
                UserFilterCopySeverityRow(
                    user_id=user_id, subcategory=subcategory, position=position, severity=severity
                )
            )
    for user_id, overrides in _OVERRIDES.items():
        for subcategory, severity in overrides.items():
            session.add(
                UserFilterSeverityOverrideRow(
                    user_id=user_id, subcategory=subcategory, severity=severity
                )
            )


def _values(engine, user_id):
    """One user's stored values, read in a fresh session."""
    with Session(engine) as session:
        rules = session.scalars(
            select(UserFilterCopyRuleRow)
            .where(UserFilterCopyRuleRow.user_id == user_id)
            .order_by(UserFilterCopyRuleRow.position)
        )
        severities = session.scalars(
            select(UserFilterCopySeverityRow)
            .where(UserFilterCopySeverityRow.user_id == user_id)
            .order_by(UserFilterCopySeverityRow.position)
        )
        overrides = session.scalars(
            select(UserFilterSeverityOverrideRow).where(
                UserFilterSeverityOverrideRow.user_id == user_id
            )
        )
        return (
            [tuple(getattr(rule, field) for field in _RULE_FIELDS) for rule in rules],
            [(row.subcategory, row.position, row.severity) for row in severities],
            {row.subcategory: row.severity for row in overrides},
        )


def _expected(user_id):
    return (
        _RULES.get(user_id, []),
        _COPY_SEVERITIES.get(user_id, []),
        _OVERRIDES.get(user_id, {}),
    )


def _preferences(connection, *rows):
    connection.executemany(
        "INSERT INTO user_filter_preferences VALUES (?, ?, 1, 0, ?)",
        [(user_id, mode, _TIME) for user_id, mode in rows],
    )
    connection.commit()


def test_values_round_trip_per_user_and_ignore_instance_changes(database, tmp_path):
    _preferences(database, (2, "copy"), (3, "copy"), (4, "live"))
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner.db'}")
    try:
        with Session(engine) as session:
            _write_values(session)
            session.commit()
        assert {user: _values(engine, user) for user in (2, 3, 4)} == {
            user: _expected(user) for user in (2, 3, 4)
        }

        database.execute(
            "INSERT INTO filter_selection_rules (id, settings_id, position, disposition, "
            "category, subcategory) VALUES ('rule-1', 1, 0, 'allow', 'language', 'swearing')"
        )
        database.execute("UPDATE mcf_severity_defaults SET severity = 'low'")
        database.execute("UPDATE filter_settings SET revision = revision + 1")
        database.commit()
        assert {user: _values(engine, user) for user in (2, 3, 4)} == {
            user: _expected(user) for user in (2, 3, 4)
        }

        with Session(engine) as session:
            session.execute(
                update(UserFilterCopyRuleRow)
                .where(UserFilterCopyRuleRow.user_id == 2, UserFilterCopyRuleRow.id == "rule-1")
                .values(disposition="allow")
            )
            for row in (
                UserFilterCopyRuleRow,
                UserFilterCopySeverityRow,
                UserFilterSeverityOverrideRow,
            ):
                session.execute(delete(row).where(row.user_id == 4))
            session.commit()
        assert _values(engine, 2)[0][0][2] == "allow"
        assert {user: _values(engine, user) for user in (3, 4)} == {
            3: _expected(3),
            4: ([], [], {}),
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "fields",
    [
        dict(row=UserFilterCopyRuleRow, id="rule", position=0, disposition="deny"),
        dict(row=UserFilterCopySeverityRow, position=0, severity="low"),
        dict(row=UserFilterSeverityOverrideRow, severity="low"),
    ],
)
def test_unknown_subcategories_are_refused_before_storage(fields):
    values = dict(fields)
    with pytest.raises(ValueError, match="Unknown MCF subcategory"):
        values.pop("row")(user_id=2, subcategory="notACategory", **values)


_COPY_RULE = "INSERT INTO user_filter_copy_rules VALUES ({})"
_COPY_SEVERITY = "INSERT INTO user_filter_copy_severities VALUES ({})"
_OVERRIDE = "INSERT INTO user_filter_severity_overrides VALUES ({})"


@pytest.mark.parametrize(
    "statement",
    [
        _COPY_RULE.format("2, 'rule-2', 0, 'allow', NULL, NULL, NULL, 'x', NULL, NULL"),
        _COPY_RULE.format("2, 'rule-1', 5, 'allow', NULL, NULL, NULL, 'x', NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', -1, 'allow', NULL, NULL, NULL, 'x', NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'skip', NULL, NULL, NULL, 'x', NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', 'text', NULL, NULL, NULL, NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, NULL, NULL, NULL, NULL, 'left'"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, 'language', 'murder', NULL, NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, NULL, 'swearing', NULL, NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, 'gore', NULL, NULL, NULL, NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, NULL, NULL, NULL, 'extreme', NULL"),
        _COPY_RULE.format("2, 'rule-3', 1, 'deny', NULL, NULL, NULL, NULL, NULL, NULL"),
        _COPY_RULE.format("3, 'rule-1', 0, 'deny', NULL, NULL, NULL, 'x', NULL, NULL"),
        _COPY_SEVERITY.format("2, 'murder', 0, 'low'"),
        _COPY_SEVERITY.format("2, 'murder', -1, 'low'"),
        _COPY_SEVERITY.format("2, 'murder', 1, 'extreme'"),
        _COPY_SEVERITY.format("2, 'swearing', 1, 'low'"),
        _COPY_SEVERITY.format("3, 'murder', 0, 'low'"),
        _OVERRIDE.format("2, 'swearing', 'low'"),
        _OVERRIDE.format("2, 'murder', 'extreme'"),
        _OVERRIDE.format("2, 'murder', NULL"),
        _OVERRIDE.format("3, 'murder', 'low'"),
    ],
)
def test_storage_refuses_invalid_or_duplicate_values(database, statement):
    _preferences(database, (2, "copy"))
    database.execute(_COPY_RULE.format("2, 'rule-1', 0, 'deny', NULL, NULL, NULL, 'x', NULL, NULL"))
    database.execute(_COPY_SEVERITY.format("2, 'swearing', 0, 'high'"))
    database.execute(_OVERRIDE.format("2, 'swearing', 'high'"))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(statement)


def test_runtime_foreign_keys_are_off_so_deleting_a_user_requires_deleting_values(
    database, tmp_path
):
    database.execute("PRAGMA foreign_keys=OFF")
    _preferences(database, (2, "copy"))
    database.execute(_OVERRIDE.format("2, 'swearing', 'high'"))
    database.execute("DELETE FROM user_filter_preferences WHERE user_id = 2")
    database.commit()
    assert database.execute("PRAGMA foreign_key_check").fetchall() == [
        ("user_filter_severity_overrides", 1, "user_filter_preferences", 0)
    ]
    path = tmp_path / "scanner.db"
    with pytest.raises(ValueError, match="foreign key"):
        adoption._inspect_database(path)
    database.execute("DELETE FROM user_filter_severity_overrides WHERE user_id = 2")
    database.commit()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION


def _seed(directory, severity):
    with sqlite3.connect(directory / "scanner.db") as connection:
        _preferences(connection, (1, "copy"))
        connection.execute(
            _COPY_RULE.format(
                f"1, 'rule-1', 0, 'deny', NULL, NULL, NULL, '{directory.name}', NULL, NULL"
            )
        )
        connection.execute(_COPY_SEVERITY.format(f"1, 'swearing', 0, '{severity}'"))
        connection.execute(_OVERRIDE.format(f"1, 'murder', '{severity}'"))


def _stored(path):
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return [
            connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in (
                "user_filter_copy_rules",
                "user_filter_copy_severities",
                "user_filter_severity_overrides",
            )
        ]


def test_backup_restore_moves_values_with_the_accounts(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    _seed(source, "high")
    expected = _stored(source / "scanner.db")
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    assert _stored(target / "scanner.db") == expected
    assert expected[0][0][7] == ("source")
