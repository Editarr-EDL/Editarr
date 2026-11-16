"""MU-14: filter writers decide under SQLite's write lock, and repeats change nothing.

A writer that reads outside a transaction can act on a state another request has
already replaced. Each writer therefore takes the write lock before its first read,
so the only place another request can commit is before the writer starts deciding.
Harness: `tests.filter_policy_http_support`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event

from scanner.db import FilterSettingsRow
from scanner.persistence.repository import filter_enforcement, filter_preferences
from scanner.settings.filter_policy.updates import FilterPolicyUpdate
from tests.filter_policy_http_support import BASE, rule, severities
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, USER

DENY = rule("deny", category="language")
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def _save_copy(app):
    assert app.mode(USER, "copy").status_code == 200
    return lambda: app.save(
        USER,
        {
            "mode": "copy",
            "revision": app.view(USER)["revision"],
            "rules": [],
            "severities": severities(swearing="low"),
        },
    )


_WRITERS = {
    "save live": (
        filter_preferences,
        "save_preferences",
        lambda app: lambda: app.live(USER, personal_rules=[rule("deny", category="sex")]),
    ),
    "save copy": (filter_preferences, "save_preferences", _save_copy),
    "own mode": (filter_preferences, "set_mode", lambda app: lambda: app.mode(USER, "copy")),
    "assigned mode": (
        filter_preferences,
        "set_mode",
        lambda app: lambda: app.mode(ADMIN, "copy", of=USER),
    ),
    "default mode": (
        filter_preferences,
        "set_default_mode",
        lambda app: lambda: app.send("PUT", f"{BASE}/default-mode", ADMIN, {"mode": "copy"}),
    ),
    "enforcement": (
        filter_enforcement,
        "set_enforcement",
        lambda app: lambda: app.enforce(OWNER, USER, [DENY["id"]]),
    ),
}


@pytest.mark.parametrize("writer", _WRITERS)
def test_every_filter_writer_reads_only_inside_its_write_transaction(
    filter_app, accounts, monkeypatch, writer
):
    filter_app.set_instance([DENY], swearing="high")
    module, name, prepare = _WRITERS[writer]
    act = prepare(filter_app)
    issued = []

    def statement(connection, cursor, sql, parameters, context, executemany):
        issued.append((sql, connection.connection.driver_connection.in_transaction))

    def commit(connection):
        issued.append(("COMMIT", True))

    real = getattr(module, name)

    def recorded(*args, **kwargs):
        event.listen(accounts.engine, "before_cursor_execute", statement)
        event.listen(accounts.engine, "commit", commit)
        try:
            return real(*args, **kwargs)
        finally:
            event.remove(accounts.engine, "before_cursor_execute", statement)
            event.remove(accounts.engine, "commit", commit)

    monkeypatch.setattr(module, name, recorded)

    assert act().status_code == 200

    cut = next((index for index, (sql, _) in enumerate(issued) if sql == "COMMIT"), len(issued))
    decided = [(sql, held) for sql, held in issued[:cut] if sql != "BEGIN IMMEDIATE"]
    assert decided
    assert [" ".join(sql.split())[:80] for sql, held in decided if not held] == []


def test_a_writer_session_holding_rows_loaded_before_an_edit_decides_on_the_edit(
    filter_app, accounts
):
    filter_app.set_instance([DENY])
    enforce = FilterPolicyUpdate(policy_revision=0, rules=[DENY["id"]])

    with accounts.factory() as db:
        held = db.get(FilterSettingsRow, 1)
        assert held.selection_rules
        filter_app.set_instance([])
        with pytest.raises(filter_preferences.FilterPreferenceConflict) as refused:
            filter_enforcement.set_enforcement(db, USER, enforce, now=NOW)

    assert refused.value.code == "unknown_filter_rule"
    assert filter_app.view(USER)["enforced_rules"] == []


def test_a_refused_writer_releases_the_write_lock_at_once(filter_app, accounts):
    unknown = FilterPolicyUpdate(policy_revision=0, rules=[str(uuid4())])

    with accounts.factory() as db:
        with pytest.raises(filter_preferences.FilterPreferenceConflict):
            filter_enforcement.set_enforcement(db, USER, unknown, now=NOW)
        saved = filter_app.live(USER, personal_rules=[rule("deny", category="sex")])

    assert saved.status_code == 200


def test_repeating_a_copy_mode_change_never_rewrites_an_existing_copy(filter_app):
    filter_app.set_instance([DENY], swearing="high")
    assert filter_app.mode(USER, "copy").status_code == 200
    view = filter_app.view(USER)
    body = {
        "mode": "copy",
        "revision": view["revision"],
        "rules": [],
        "severities": severities(swearing="low"),
    }
    assert filter_app.save(USER, body).status_code == 200
    edited = filter_app.view(USER)

    assert filter_app.ok("PUT", f"{BASE}/default-mode", ADMIN, {"mode": "copy"}) == {"mode": "copy"}
    assert filter_app.view(USER) == edited

    assert filter_app.mode(ADMIN, "copy", of=USER).status_code == 200
    assert filter_app.mode(USER, "copy").status_code == 200
    again = filter_app.view(USER)
    assert (again["rules"], again["severities"]) == (edited["rules"], edited["severities"])
