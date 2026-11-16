import io
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import cli
from scanner.commands import admin
from scanner.db import Base


@pytest.fixture
def cli_database(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    initialized: list[None] = []

    def initialize() -> None:
        initialized.append(None)
        Base.metadata.create_all(engine)

    monkeypatch.setattr(admin, "init_db", initialize)
    monkeypatch.setattr(admin, "get_session_factory", lambda: factory)
    monkeypatch.setattr(cli.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cli.settings, "log_dir", tmp_path / "logs")
    return factory, initialized


def _run(monkeypatch, argv: list[str], *, stdin: str = "") -> None:
    monkeypatch.setattr(sys, "argv", ["editarr", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    cli.main()
