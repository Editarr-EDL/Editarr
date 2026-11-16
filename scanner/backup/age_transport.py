"""Optional age transport with a private, bounded passphrase channel."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HELPER = Path(__file__).with_name("age_passphrase.exp")


def _native_helper() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    if root is None:
        return None
    name = "backup-age.exe" if os.name == "nt" else "backup-age"
    return Path(root) / "helpers" / name


class AgeUnavailable(RuntimeError):
    """The optional authenticated backup transport did not complete."""


def validate_age_header(path: Path) -> None:
    """Reject a writer with a different container or nonapproved scrypt factor."""
    with path.open("rb") as stream:
        version = stream.readline(64)
        stanza = stream.readline(128)
    if version != b"age-encryption.org/v1\n" or not re.fullmatch(
        rb"-> scrypt [A-Za-z0-9+/]{22} 18\n", stanza
    ):
        raise AgeUnavailable("Unsupported age passphrase header or work factor")


def run_age(source: Path, target: Path, passphrase: str, *, decrypt: bool = False) -> None:
    """Use a bundled native helper or the source-install prompt bridge."""
    if (
        not isinstance(passphrase, str)
        or not passphrase
        or "\n" in passphrase
        or "\r" in passphrase
        or "\x00" in passphrase
        or len(passphrase.encode("utf-8")) > 4096
    ):
        raise AgeUnavailable("A nonempty, single-line passphrase is required")
    helper = _native_helper()
    if helper is not None:
        if not helper.is_file():
            raise AgeUnavailable("The optional age transport is unavailable")
        command = [str(helper), "decrypt" if decrypt else "encrypt", str(source), str(target)]
    else:
        executable = shutil.which("age")
        expect = shutil.which("expect")
        if os.name != "posix" or executable is None or expect is None or not _HELPER.is_file():
            raise AgeUnavailable("The optional age transport is unavailable")
        command = [
            expect,
            str(_HELPER),
            executable,
            "decrypt" if decrypt else "encrypt",
            str(source),
            str(target),
        ]
    completed = False
    try:
        result = subprocess.run(
            command,
            input=passphrase.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=70,
            check=False,
        )
        if result.returncode:
            raise AgeUnavailable("The age operation failed")
        completed = True
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AgeUnavailable("The age operation failed") from error
    finally:
        if not completed:
            target.unlink(missing_ok=True)
