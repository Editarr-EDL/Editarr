import io
import sys

import pytest

from scanner import cli
from tests.cli_support import _run
from tests.cli_support import cli_database as cli_database
from tests.multi_user_access_support import add_linked_owner


def test_admin_status_initializes_schema_and_names_no_account(monkeypatch, capsys, cli_database):
    factory, initialized = cli_database

    _run(monkeypatch, ["admin", "status"])
    assert capsys.readouterr().out == "Administrator status: setup required.\n"
    assert initialized

    with factory() as db:
        add_linked_owner(db, "Admin")
    _run(monkeypatch, ["admin", "status"])
    assert capsys.readouterr().out == "Administrator status: configured.\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["admin", "create", "--username", "Admin", "--password-stdin"],
        ["admin", "reset-password", "--password-stdin"],
        ["admin", "rename", "--username", "Other"],
    ],
)
def test_no_admin_command_creates_or_recovers_a_password_owner(
    monkeypatch, capsys, cli_database, argv
):
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, argv, stdin="correct-horse-battery-staple\n")

    assert exit_info.value.code == 2
    assert f"invalid choice: '{argv[1]}'" in capsys.readouterr().err


def test_write_export_refuses_tty_stdin_and_a_plaintext_password_option(
    monkeypatch, capsys, cli_database
):
    factory, _ = cli_database
    _run(monkeypatch, ["admin", "status"])
    capsys.readouterr()
    with factory() as db:
        add_linked_owner(db, "Admin")

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "argv", ["editarr", "admin", "write-export", "1", "--password-stdin"])
    monkeypatch.setattr(sys, "stdin", TtyInput("correct-horse-battery-staple\n"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    assert "requires non-interactive" in capsys.readouterr().err

    secret = "this-must-not-appear-in-terminal-output"
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, ["admin", "write-export", "1", "--password", secret])
    assert exit_info.value.code == 2
    output = capsys.readouterr()
    assert "--password is not supported" in output.err
    assert secret not in output.out + output.err


@pytest.mark.parametrize(
    "argv",
    [
        ["auth", "show"],
        ["auth", "set-method", "external"],
        ["auth", "set-required", "disabled_for_local_addresses"],
    ],
)
def test_no_cli_command_configures_an_authentication_bypass(
    monkeypatch, capsys, cli_database, argv
):
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, argv)

    assert exit_info.value.code == 2
    assert "invalid choice: 'auth'" in capsys.readouterr().err


def test_an_owner_without_a_jellyfin_link_is_refused_before_any_password_prompt(
    monkeypatch, capsys, cli_database
):
    from scanner.commands import admin
    from scanner.db import UserProviderIdentityRow

    factory, _ = cli_database
    _run(monkeypatch, ["admin", "status"])
    capsys.readouterr()
    with factory() as db:
        owner = add_linked_owner(db, "Admin")
        db.query(UserProviderIdentityRow).filter_by(user_id=owner).delete()
        db.commit()
    prompts: list[str] = []
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: prompts.append(prompt) or "x")

    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, ["admin", "write-export", "1"])
    assert exit_info.value.code == 1
    assert "migration recover-export" in capsys.readouterr().err
    assert prompts == []
