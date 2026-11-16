"""MU-14: filter reads see one commit, and default-mode changes never overwrite a copy.

pysqlite runs each SELECT outside a transaction, so a read made of several SELECTs can
straddle another request's commit. A writer holds the write lock from before its first
read, so another request can only commit before that lock. Races are injected
deterministically before a chosen SQL statement through separate HTTP requests.
Harness: `tests.filter_policy_http_support`.
"""

from __future__ import annotations

from itertools import cycle

import pytest

from scanner.db import FilterSettingsRow
from scanner.persistence.repository import filter_preferences
from tests.filter_policy_http_support import BASE, code, rule, severities, takes_write_lock
from tests.filter_policy_http_support import accounts as accounts
from tests.filter_policy_http_support import before_statement as before_statement
from tests.filter_policy_http_support import filter_app as filter_app
from tests.multi_user_access_support import ADMIN, OWNER, USER

STALE = (409, "stale_settings_revision")


def _reads_user_row(statement, parameters) -> bool:
    return "WHERE user_filter_preferences.user_id = ?" in statement and parameters == (USER,)


_READS = {
    "enforced policy": filter_preferences.enforced_filter_policy,
    "preferences view": filter_preferences.filter_preferences_view,
}


@pytest.mark.parametrize("read", _READS)
def test_a_read_racing_an_enforced_rule_edit_returns_one_commit(
    filter_app, accounts, before_statement, read
):
    deny = rule("deny", category="language", severity="high")
    filter_app.set_instance([deny])
    assert filter_app.enforce(OWNER, USER, [deny["id"]]).status_code == 200
    raced = before_statement(
        _reads_user_row, lambda: filter_app.set_instance([{**deny, "severity": "low"}])
    )

    with accounts.factory() as db:
        during = _READS[read](db, USER)
    with accounts.factory() as db:
        after = _READS[read](db, USER)

    assert raced["left"] == 0
    assert during == after


@pytest.mark.parametrize("read", _READS)
def test_a_session_still_holding_rows_loaded_before_an_edit_reads_the_edit(
    filter_app, accounts, read
):
    deny = rule("deny", category="language", severity="high")
    filter_app.set_instance([deny])
    assert filter_app.enforce(OWNER, USER, [deny["id"]]).status_code == 200

    with accounts.factory() as db:
        held = db.get(FilterSettingsRow, 1)
        assert held.selection_rules
        before = _READS[read](db, USER)
        filter_app.set_instance([{**deny, "severity": "low"}])
        again = _READS[read](db, USER)
    with accounts.factory() as db:
        after = _READS[read](db, USER)

    assert again == after != before


def test_a_read_that_every_write_overtakes_is_refused_as_stale(filter_app, before_statement):
    deny = rule("deny", category="language", severity="high")
    filter_app.set_instance([deny])
    severity = cycle(["low", "high"])
    before_statement(
        _reads_user_row,
        lambda: filter_app.set_instance([{**deny, "severity": next(severity)}]),
        times=1000,
    )

    assert code(filter_app.send("GET", f"{BASE}/personal", USER)) == STALE
    assert code(filter_app.send("GET", f"{BASE}/users/{USER}", ADMIN)) == STALE
    assert code(filter_app.send("POST", f"{BASE}/apply", USER, {"filters": []})) == STALE


def test_a_default_mode_change_keeps_a_copy_chosen_before_it_took_the_lock(
    filter_app, before_statement
):
    filter_app.set_instance([rule("deny", category="language")], swearing="high")
    chosen = {}

    def choose_copy():
        assert filter_app.mode(USER, "copy").status_code == 200
        revision = filter_app.view(USER)["revision"]
        body = {
            "mode": "copy",
            "revision": revision,
            "rules": [],
            "severities": severities(swearing="low"),
        }
        assert filter_app.save(USER, body).status_code == 200
        chosen.update(filter_app.view(USER))

    raced = before_statement(takes_write_lock, choose_copy)

    changed = filter_app.send("PUT", f"{BASE}/default-mode", ADMIN, {"mode": "copy"})

    assert raced["left"] == 0
    assert changed.json() == {"mode": "copy"}
    assert filter_app.view(USER) == chosen
