"""Focused verification for recoverable AES-GCM CSRF session state."""

from __future__ import annotations

import base64
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner.db import AuthSessionRow, Base
from scanner.security import auth_crypto
from scanner.security.auth_crypto import (
    CsrfEncryptionKey,
    CsrfEnvelopeError,
    CsrfKeyError,
    decrypt_csrf_token,
    encrypt_csrf_token,
    load_csrf_encryption_key,
)
from scanner.security.auth_repository import (
    authenticate_session,
    issue_session,
    recover_csrf_token,
    revoke_sessions_with_other_csrf_key_id,
)
from tests.multi_user_access_support import add_linked_owner

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DIGEST = "a" * 64


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def test_envelope_has_exact_encoding_fresh_nonce_and_session_binding(monkeypatch):
    key = CsrfEncryptionKey.from_bytes(bytes(range(32)))
    token = _encoded(bytes(range(32, 64)))
    nonces = iter((b"a" * 12, b"b" * 12))
    monkeypatch.setattr("scanner.security.auth_crypto.os.urandom", lambda size: next(nonces))

    first = encrypt_csrf_token(token, _DIGEST, key)
    second = encrypt_csrf_token(token, _DIGEST, key)

    assert first.startswith(f"v1.{key.key_id}.")
    assert first != second
    assert decrypt_csrf_token(first, _DIGEST, key) == token
    with pytest.raises(CsrfEnvelopeError):
        decrypt_csrf_token(first, "b" * 64, key)
    with pytest.raises(CsrfEnvelopeError):
        decrypt_csrf_token(first[:-1] + "A", _DIGEST, key)
    with pytest.raises(CsrfEnvelopeError):
        decrypt_csrf_token(first, _DIGEST, CsrfEncryptionKey.from_bytes(b"z" * 32))


def test_key_loader_environment_is_strict_and_takes_precedence(tmp_path):
    value = _encoded(b"e" * 32)
    file_path = tmp_path / "secrets" / "auth-csrf.key"
    file_path.parent.mkdir(mode=0o700)
    file_path.write_text(_encoded(b"f" * 32) + "\n")
    file_path.chmod(0o600)

    environment = {"EDITARR_AUTH_CSRF_ENCRYPTION_KEY": value}
    key = load_csrf_encryption_key(tmp_path, environment=environment)

    assert key.key_id == CsrfEncryptionKey.from_bytes(b"e" * 32).key_id
    for invalid in ("", value + "=", _encoded(b"x" * 31), "!" * 43):
        with pytest.raises(CsrfKeyError):
            load_csrf_encryption_key(
                tmp_path, environment={"EDITARR_AUTH_CSRF_ENCRYPTION_KEY": invalid}
            )


def test_key_loader_creates_one_private_file_concurrently_and_rejects_unsafe_files(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        keys = list(executor.map(lambda _: load_csrf_encryption_key(tmp_path), range(4)))
    key_file = tmp_path / "secrets" / "auth-csrf.key"

    assert {key.key_id for key in keys} == {keys[0].key_id}
    contents = key_file.read_bytes()
    assert contents.endswith(b"\n")
    assert contents.count(b"\n") == 1
    assert b"\r" not in contents
    assert len(contents[:-1]) == 43
    assert stat.S_ISREG(key_file.stat().st_mode)
    assert not list(key_file.parent.glob(f".{key_file.name}.*.tmp"))

    if os.name == "posix":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(key_file.parent.stat().st_mode) & 0o077 == 0
        key_file.chmod(0o640)
        with pytest.raises(CsrfKeyError):
            load_csrf_encryption_key(tmp_path)


def test_key_loader_rejects_symlink_without_disclosing_key_material(tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text(_encoded(b"x" * 32) + "\n")
    target.chmod(0o600)
    (secrets_dir / "auth-csrf.key").symlink_to(target)

    with pytest.raises(CsrfKeyError) as error:
        load_csrf_encryption_key(tmp_path)
    assert _encoded(b"x" * 32) not in str(error.value)


def test_windows_key_validation_uses_file_type_not_posix_mode_bits(monkeypatch, tmp_path):
    key_file = tmp_path / "auth-csrf.key"
    encoded = _encoded(b"w" * 32)
    key_file.write_bytes((encoded + "\r\n").encode("ascii"))
    monkeypatch.setattr(
        auth_crypto.os,
        "lstat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFREG | 0o666),
    )

    assert auth_crypto._read_private_key_file(key_file, platform_name="nt") == b"w" * 32
    with pytest.raises(CsrfKeyError, match="permissions must be owner-only"):
        auth_crypto._read_private_key_file(key_file, platform_name="posix")


def test_key_loader_uses_binary_creation_and_accepts_only_canonical_crlf(monkeypatch, tmp_path):
    binary_flag = 0x8000
    real_open = auth_crypto.os.open
    seen_flags: list[int] = []
    monkeypatch.setattr(auth_crypto.os, "O_BINARY", binary_flag, raising=False)

    def binary_safe_open(path, flags, mode):
        seen_flags.append(flags)
        return real_open(path, flags & ~binary_flag, mode)

    monkeypatch.setattr(auth_crypto.os, "open", binary_safe_open)
    assert load_csrf_encryption_key(tmp_path)
    assert any(flags & binary_flag for flags in seen_flags)

    key_file = tmp_path / "compat.key"
    key_file.write_bytes((_encoded(b"c" * 32) + "\r\n").encode("ascii"))
    assert auth_crypto._read_private_key_file(key_file, platform_name="nt") == b"c" * 32

    key_file.write_bytes((_encoded(b"c" * 32) + "\r\r\n").encode("ascii"))
    with pytest.raises(CsrfKeyError, match="malformed"):
        auth_crypto._read_private_key_file(key_file, platform_name="nt")


def test_windows_key_validation_still_rejects_non_regular_files(monkeypatch, tmp_path):
    key_file = tmp_path / "auth-csrf.key"
    key_file.write_text(_encoded(b"w" * 32) + "\n")
    monkeypatch.setattr(
        auth_crypto.os,
        "lstat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFLNK | 0o600),
    )

    with pytest.raises(CsrfKeyError, match="regular file"):
        auth_crypto._read_private_key_file(key_file, platform_name="nt")


def test_recovery_rejects_digest_mismatch_and_key_replacement_revokes_sessions(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        add_linked_owner(db, "Admin")
        first_key = CsrfEncryptionKey.from_bytes(b"a" * 32)
        issued = issue_session(db, remember_me=False, now=_NOW, csrf_encryption_key=first_key)
        session = authenticate_session(db, issued.token, now=_NOW)
        assert session is not None
        assert recover_csrf_token(db, session, csrf_encryption_key=first_key) == issued.csrf_token

        row = db.get(AuthSessionRow, issued.id)
        row.csrf_token_digest = "0" * 64
        db.commit()
        session = authenticate_session(db, issued.token, now=_NOW)
        assert session is not None
        assert recover_csrf_token(db, session, csrf_encryption_key=first_key) is None
        assert db.get(AuthSessionRow, issued.id).revoked_at is not None

        second = issue_session(db, remember_me=False, now=_NOW, csrf_encryption_key=first_key)
        replacement = CsrfEncryptionKey.from_bytes(b"b" * 32)
        assert revoke_sessions_with_other_csrf_key_id(db, replacement.key_id, now=_NOW) == 1
        assert authenticate_session(db, second.token, now=_NOW) is None
    finally:
        db.close()
        engine.dispose()
