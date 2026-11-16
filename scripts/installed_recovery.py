"""Installed-artifact authentication, backup restore, and the absence of owner recovery (MU-45).

Black-box like its caller: it drives only the installed ``editarr`` command and its HTTP
API, never ``scanner`` imports. One source installation is set up through the loopback
Jellyfin stand-in and gets a Jellyfin-linked owner, a session, and an API key. The
offline CLI exports a backup, refuses to restore over the source, and restores into an
empty data directory. The restored installation must refuse the source's cookie and
accept the owner's Jellyfin sign-in and key. There is no local password: the CLI has no
password reset, running or stopped, and local sign-in is gone, so the owner's Jellyfin
sign-in, session, and key keep working.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from scripts.jellyfin_stand_in import ADMIN, ADMIN_PASSWORD
from scripts.verify_installed_release import (
    Evidence,
    _available_port,
    _environment,
    _request,
    _start,
    _stop,
    _wait_ready,
)

_NEW_PASSWORD = "MU45-installed-recovered-password!"
_NO_RESET = "invalid choice: 'reset-password'"
_SESSION = "editarr_session"


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith((".lock", "-wal", "-shm"))
    }


def _cli(editarr: Path, data: Path, environment: dict[str, str], *arguments: str, stdin=""):
    return subprocess.run(
        [str(editarr), "--data-dir", str(data), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        env=environment,
        cwd=data.parent,
        timeout=300,
        check=False,
    )


def _session(origin: str, path: str, body: dict[str, Any], expected: int):
    jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    status, _headers, payload = _request(origin + path, opener=opener, data=body)
    if status != expected:
        raise RuntimeError(f"{path} returned HTTP {status}, expected {expected}")
    token = next((cookie.value for cookie in jar if cookie.name == _SESSION), None)
    return opener, json.loads(payload), token


def _status(origin: str, *, cookie: str | None = None, key: str | None = None) -> int:
    headers = {}
    if cookie:
        headers["Cookie"] = f"{_SESSION}={cookie}"
    if key:
        headers["X-Api-Key"] = key
    try:
        with urlopen(Request(origin + "/api/system/version", headers=headers), timeout=15) as r:
            return r.status
    except HTTPError as error:
        return error.code


class _Instance:
    """One installed service on its own data directory and port."""

    def __init__(self, editarr: Path, data: Path, environment: dict[str, str]):
        self.editarr, self.data, self.environment = editarr, data, environment
        self.port = _available_port()
        self.origin = f"http://localhost:{self.port}"

    def __enter__(self):
        self.process, self.log = _start(self.editarr, self.port, self.data.parent, self.environment)
        try:
            _wait_ready(self.origin, self.process)
        except BaseException:
            # A failed __enter__ never reaches __exit__; the caller then deletes this data.
            _stop(self.process)
            raise
        return self

    def __exit__(self, *_exc):
        _stop(self.process)


def recovery_smoke(
    editarr: Path,
    dependencies: Path | None,
    root: Path,
    evidence: Evidence,
    *,
    jellyfin: str,
    install: Path | None = None,
):
    """Run the installed restore and recovery sequence and record its checks.

    ``install`` names a native runtime directory; without it, ``editarr`` is a wheel
    environment's console script.
    """
    checks: dict[str, Any] = {}
    evidence.checks["installed_recovery"] = checks
    source, target = root / "recovery-source" / "data", root / "recovery-target" / "data"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    native = install is not None
    install = install or editarr.parent.parent
    source_env = _environment(install, dependencies, source, native=native)
    target_env = _environment(install, dependencies, target, native=native)
    archive = root / "recovery-archive" / "installed.backup"
    archive.parent.mkdir()
    ports: list[int] = []
    evidence.checks["installed_recovery_ports"] = ports

    with _Instance(editarr, source, source_env) as instance:
        ports.append(instance.port)
        credentials = {
            "server_url": jellyfin,
            "username": ADMIN,
            "password": ADMIN_PASSWORD,
            "remember_me": False,
        }
        opener, setup, cookie = _session(instance.origin, "/api/auth/setup", credentials, 201)
        status, _h, body = _request(
            instance.origin + "/api/auth/api-key",
            opener=opener,
            csrf=setup["csrf_token"],
            data={"reauthentication": {"password": ADMIN_PASSWORD}},
        )
        if status != 200:
            raise RuntimeError(f"installed key issue returned HTTP {status}")
        key = json.loads(body)["api_key"]
        checks["source"] = {
            "setup": 201,
            "cookie": _status(instance.origin, cookie=cookie),
            "key": _status(instance.origin, key=key),
        }

    exported = _cli(editarr, source, source_env, "backup", "export", str(archive))
    checks["export"] = {"exit": exported.returncode, "archive": archive.is_file()}
    before = _tree(source)
    over_source = _cli(editarr, source, source_env, "backup", "restore", str(archive), "--confirm")
    checks["restore_over_source"] = {
        "exit": over_source.returncode,
        "reason": "restore_target_has_accounts" in over_source.stderr,
        "unchanged": _tree(source) == before,
    }
    restored = _cli(editarr, target, target_env, "backup", "restore", str(archive), "--confirm")
    checks["restore_into_empty"] = {
        "exit": restored.returncode,
        "review_notice": "Review media paths and source connections" in restored.stdout,
    }

    with _Instance(editarr, target, target_env) as instance:
        ports.append(instance.port)
        _opener, _login, fresh = _session(
            instance.origin,
            "/api/auth/jellyfin/login",
            {"username": ADMIN, "password": ADMIN_PASSWORD},
            200,
        )
        checks["restored"] = {
            "source_cookie": _status(instance.origin, cookie=cookie),
            "owner_login": 200,
            "fresh_cookie": _status(instance.origin, cookie=fresh),
            "key": _status(instance.origin, key=key),
        }
        refused = _cli(
            editarr,
            target,
            target_env,
            "admin",
            "reset-password",
            "--password-stdin",
            stdin=f"{_NEW_PASSWORD}\n",
        )
        checks["recovery_while_running"] = {
            "exit": refused.returncode,
            "absent": _NO_RESET in refused.stderr,
        }

    recovered = _cli(
        editarr,
        target,
        target_env,
        "admin",
        "reset-password",
        "--password-stdin",
        stdin=f"{_NEW_PASSWORD}\n",
    )
    checks["offline_recovery"] = {
        "exit": recovered.returncode,
        "absent": _NO_RESET in recovered.stderr,
    }
    with _Instance(editarr, target, target_env) as instance:
        ports.append(instance.port)
        new_password, _h, _b = _request(
            instance.origin + "/api/auth/login",
            data={"username": ADMIN, "password": _NEW_PASSWORD},
        )
        _session(
            instance.origin,
            "/api/auth/jellyfin/login",
            {"username": ADMIN, "password": ADMIN_PASSWORD},
            200,
        )
        checks["after_recovery"] = {
            "new_password": new_password,
            "jellyfin_login": 200,
            "pre_recovery_cookie": _status(instance.origin, cookie=fresh),
            "key": _status(instance.origin, key=key),
        }

    expected = {
        "source": {"setup": 201, "cookie": 200, "key": 200},
        "export": {"exit": 0, "archive": True},
        "restore_over_source": {"exit": 1, "reason": True, "unchanged": True},
        "restore_into_empty": {"exit": 0, "review_notice": True},
        "restored": {"source_cookie": 401, "owner_login": 200, "fresh_cookie": 200, "key": 200},
        "recovery_while_running": {"exit": 2, "absent": True},
        "offline_recovery": {"exit": 2, "absent": True},
        "after_recovery": {
            "new_password": 404,
            "jellyfin_login": 200,
            "pre_recovery_cookie": 200,
            "key": 200,
        },
    }
    if checks != expected:
        raise RuntimeError("installed recovery checks differ: " + json.dumps(checks))
