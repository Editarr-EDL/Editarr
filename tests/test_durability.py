"""Atomic file replacement shared by settings and backup state stores."""

import os
import stat

import pytest

from scanner.runtime.durability import atomic_write_bytes


def test_atomic_write_bytes_replaces_with_a_private_file(tmp_path):
    target = tmp_path / "state.json"
    target.write_bytes(b"old")

    atomic_write_bytes(target, b"new", prefix=".state-", sync_dir=True)

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_bytes_keeps_the_previous_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_bytes(b"old")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(target, b"new", prefix=".state-", suffix=".tmp", mode=0o640)

    assert target.read_bytes() == b"old"
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]
