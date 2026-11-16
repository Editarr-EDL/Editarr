#!/usr/bin/env python3
"""Find and redact credentials in retained verification evidence.

Every file under the evidence roots is read as bytes, and zip archives (Playwright's
``trace.zip`` included) are read member by member, recursively. Credential values are
the ``--secret`` arguments plus every value found in a credential position: an
``editarr_*`` cookie, an ``X-CSRF-Token``/``X-Api-Key``/``Authorization`` header (also
after a log line's timestamp), or a JSON password, token, or key field. Each value is
then searched for everywhere, because a trace repeats a typed password or a session
token without its label.

``--redact`` rewrites files and archive members with each value replaced, then scans
again. The exit status is 1 while any value remains.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

REDACTED = b"[REDACTED]"
_MINIMUM = 12
_Q = rb'\\*"'  # a quote, possibly escaped inside a JSON string such as postData
_LABELLED = [
    re.compile(rb"\beditarr_[a-z_]+=([A-Za-z0-9_\-.~%]+)"),
    re.compile(
        rb"(?i)"
        + _Q
        + rb"name"
        + _Q
        + rb"\s*:\s*"
        + _Q
        + rb"(?:x-csrf-token|x-api-key|authorization)"
        + _Q
        + rb"\s*,\s*"
        + _Q
        + rb"value"
        + _Q
        + rb"\s*:\s*"
        + _Q
        + rb'([^"\\]+)'
    ),
    # A header line, possibly after a log timestamp; an Authorization scheme is not the secret.
    re.compile(
        rb"(?im)^\s*(?:\d{4}-\d\d-\d\dT\S+\s+)?(?:x-csrf-token|x-api-key|authorization)\s*:"
        rb"\s*(?:(?:bearer|basic)\s+)?(\S+)"
    ),
    re.compile(
        rb"(?i)" + _Q + rb"(?:password|new_password|current_password|confirmation|csrf_token"
        rb"|api_key|token|secret)" + _Q + rb"\s*:\s*" + _Q + rb'([^"\\]+)'
    ),
]


def _members(data: bytes, name: str) -> Iterator[tuple[str, bytes]]:
    """Yield (location, bytes) for a file and, recursively, its archive members."""
    yield name, data
    if not data.startswith(b"PK\x03\x04"):
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    with archive:
        for info in archive.infolist():
            if not info.is_dir():
                yield from _members(archive.read(info), f"{name}!{info.filename}")


def _files(roots: list[Path]) -> list[Path]:
    return sorted(
        path
        for root in roots
        for path in ([root] if root.is_file() else root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    )


def harvest(data: bytes) -> set[bytes]:
    """Credential values that appear in a credential position."""
    values = set()
    for pattern in _LABELLED:
        for match in pattern.finditer(data):
            value = match.group(1)
            if len(value) >= _MINIMUM and REDACTED not in value:
                values.add(value)
    return values


def known_secrets(roots: list[Path], given: list[str]) -> set[bytes]:
    found = {value.encode() for value in given if value}
    for path in _files(roots):
        for _location, data in _members(path.read_bytes(), str(path)):
            found |= harvest(data)
    return found


def scan(roots: list[Path], secrets: set[bytes]) -> list[dict[str, object]]:
    """Every location holding a secret, by archive member; values are not reported."""
    findings = []
    for path in _files(roots):
        for location, data in _members(path.read_bytes(), str(path)):
            hits = sum(1 for secret in secrets if secret in data)
            if hits:
                findings.append({"location": location, "secrets": hits})
    return findings


def scrub(data: bytes, secrets: set[bytes]) -> bytes:
    """``data`` with every secret replaced, inside zip archive members too."""
    if data.startswith(b"PK\x03\x04"):
        try:
            source = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            pass
        else:
            output = io.BytesIO()
            with source, zipfile.ZipFile(output, "w") as rewritten:
                for info in source.infolist():
                    rewritten.writestr(info, scrub(source.read(info), secrets))
            return output.getvalue()
    for secret in sorted(secrets, key=len, reverse=True):
        data = data.replace(secret, REDACTED)
    return data


def redact(roots: list[Path], secrets: set[bytes]) -> list[str]:
    """Rewrite every file that holds a secret; return the rewritten paths."""
    changed = []
    for path in _files(roots):
        data = path.read_bytes()
        cleaned = scrub(data, secrets)
        if cleaned != data:
            path.write_bytes(cleaned)
            changed.append(str(path))
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--secret", action="append", default=[], help="A value known to be secret")
    parser.add_argument("--redact", action="store_true")
    args = parser.parse_args(argv)
    secrets = known_secrets(args.roots, args.secret)
    redacted = redact(args.roots, secrets) if args.redact else []
    findings = scan(args.roots, secrets)
    print(
        json.dumps(
            {
                "files": len(_files(args.roots)),
                "secrets": len(secrets),
                "redacted": redacted,
                "remaining": findings,
            },
            indent=1,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
