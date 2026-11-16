#!/usr/bin/env python3
"""Isolated real TLS proxy fixtures; import ProxyFixture or run the smoke matrix."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from evidence_secrets import harvest, scrub
from verify_installed_release import _available_port, _environment, _stop

TEMPLATES = Path(__file__).resolve().parents[1] / "docs/examples/reverse-proxy"


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


class ProxyFixture:
    """Owns only temporary files/process groups. Backend survives proxy reconfigure()."""

    def __init__(
        self,
        kind,
        editarr,
        dependencies,
        prefix="/",
        *,
        cli_port_override=False,
        extra_environment=None,
        log_directory=None,
        label="fixture",
    ):
        self.extra_environment = dict(extra_environment or {})
        self.cli_port_override = cli_port_override
        self.kind = kind
        self.binary = shutil.which(kind)
        if self.binary is None:
            raise RuntimeError(f"Required proxy binary missing: {kind}")
        self.editarr = Path(editarr).resolve()
        self.dependencies = Path(dependencies).resolve() if dependencies is not None else None
        self.prefix = self._route(prefix)
        self.port = _available_port()
        self.upstream_port = _available_port()
        while self.upstream_port == self.port:
            self.upstream_port = _available_port()
        self.root = Path(tempfile.mkdtemp(prefix=f"Editarr {kind} TLS "))
        self.log_directory = Path(log_directory) if log_directory is not None else None
        self.log_prefix = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{label}-"
        self.pumps = []
        self.origin = f"https://localhost:{self.port}"
        self.backend = self.proxy = None
        self.ca = self.root / "ca.pem"
        self.cert = self.root / "server.pem"
        self.key = self.root / "server.key"

    def __enter__(self):
        try:
            self._certificates()
            self.context = ssl.create_default_context(cafile=str(self.ca))
            self.data = self.root / "data"
            self.data.mkdir()
            environment = _environment(
                self.editarr.parent.parent,
                self.dependencies,
                self.data,
                native=self.dependencies is None,
            )
            environment.update(
                {
                    "EDITARR_BASE_URL": self.origin + self.prefix,
                    "EDITARR_AUTH_TRUSTED_PROXY_NETWORKS": '["127.0.0.1/32"]',
                }
            )
            environment.update(self.extra_environment)
            (self.data / "deployment.json").write_text(
                json.dumps({"version": 1, "port": self.upstream_port})
            )
            command = [str(self.editarr)]
            if self.cli_port_override:
                command += ["--port", str(self.upstream_port)]
            self.backend = self._spawn(command, "backend.log", env=environment)
            self.reconfigure()
            self.wait_ready()
            return self
        except BaseException:
            self.close()
            raise

    def _certificates(self):
        openssl = shutil.which("openssl")
        if openssl is None:
            raise RuntimeError("Required openssl binary missing")
        run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "2",
                "-subj",
                "/CN=Editarr isolated fixture CA",
                "-keyout",
                str(self.root / "ca.key"),
                "-out",
                str(self.ca),
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ]
        )
        run(
            [
                openssl,
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(self.key),
                "-out",
                str(self.root / "server.csr"),
            ]
        )
        extensions = self.root / "extensions.cnf"
        extensions.write_text(
            "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1\n"
            "extendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n"
        )
        run(
            [
                openssl,
                "x509",
                "-req",
                "-days",
                "2",
                "-in",
                str(self.root / "server.csr"),
                "-CA",
                str(self.ca),
                "-CAkey",
                str(self.root / "ca.key"),
                "-CAcreateserial",
                "-extfile",
                str(extensions),
                "-out",
                str(self.cert),
            ]
        )
        for private in (self.key, self.root / "ca.key"):
            private.chmod(0o600)

    def chromium_certificate_arg(self):
        """Trust only this generated leaf SPKI in a disposable Chromium process."""
        public_key = run(
            [shutil.which("openssl"), "x509", "-in", str(self.cert), "-pubkey", "-noout"]
        ).stdout
        der = subprocess.run(
            [shutil.which("openssl"), "pkey", "-pubin", "-outform", "DER"],
            input=public_key.encode(),
            capture_output=True,
            check=True,
        ).stdout
        pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
        return "--ignore-certificate-errors-spki-list=" + pin

    @staticmethod
    def _route(prefix):
        route = "" if prefix in {"", "/"} else prefix
        if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", route) or any(
            part in {".", ".."} for part in route.split("/")
        ):
            raise ValueError("Invalid route prefix")
        return route

    def reconfigure(self, *, upstream_port=None, prefix=None):
        """Validate candidate before stopping this fixture's proxy; never touches backend.

        Use after Settings/Restart to follow the new upstream/prefix. Public TLS port
        and certificate remain stable. Does not alter the app's saved configuration.
        """
        upstream = self.upstream_port if upstream_port is None else upstream_port
        route = self.prefix if prefix is None else self._route(prefix)
        if not 1 <= upstream <= 65535 or not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", route):
            raise ValueError("Invalid upstream port or route prefix")
        name = "nginx.conf" if self.kind == "nginx" else "Caddyfile"
        template = (TEMPLATES / (name + ".template")).read_text()
        match = (
            ("^/" if not route else "^" + re.escape(route) + "(?:/|$)")
            if self.kind == "nginx"
            else ("/*" if not route else f"{route} {route}/*")
        )
        values = dict(
            ROOT=self.root,
            PORT=self.port,
            HOST="localhost",
            CERT=self.cert,
            KEY=self.key,
            UPSTREAM=upstream,
            MATCH=match,
        )
        for key, value in values.items():
            template = template.replace(f"@@{key}@@", str(value))
        candidate = self.root / (name + ".candidate")
        candidate.write_text(template)
        env = dict(
            os.environ,
            XDG_DATA_HOME=str(self.root / "xdg-data"),
            XDG_CONFIG_HOME=str(self.root / "xdg-config"),
        )
        if self.kind == "nginx":
            validate = [self.binary, "-t", "-p", str(self.root), "-c", str(candidate)]
        else:
            validate = [
                self.binary,
                "validate",
                "--adapter",
                "caddyfile",
                "--config",
                str(candidate),
            ]
        run(validate, env=env)
        if self.proxy is not None:
            _stop(self.proxy)
        config = self.root / name
        candidate.replace(config)
        command = (
            [self.binary, "-p", str(self.root), "-c", str(config), "-g", "daemon off;"]
            if self.kind == "nginx"
            else [self.binary, "run", "--adapter", "caddyfile", "--config", str(config)]
        )
        self.proxy = self._spawn(command, "proxy.log", env=env)
        self.prefix, self.upstream_port = route, upstream

    def request(self, path, *, method="GET", body=None, headers=None, ipv6=False):
        """TLS certificate validation enabled; no redirect following or cookie magic."""
        connection = http.client.HTTPSConnection(
            "::1" if ipv6 else "127.0.0.1", self.port, context=self.context, timeout=5
        )
        request_headers = {"Host": f"localhost:{self.port}", **(headers or {})}
        if body is not None:
            body = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def wait_ready(self):
        for _ in range(150):
            if self.backend.poll() is not None or self.proxy.poll() is not None:
                raise RuntimeError("Fixture process exited; logs: " + str(self.root))
            try:
                if self.request(self.prefix + "/api/auth/status")[0] == 200:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        raise RuntimeError("Fixture readiness timed out")

    @property
    def backend_log(self):
        return self.root / "backend.log"

    def _spawn(self, command, log_name, env=None):
        """Start an owned process group whose output lines get UTC timestamps in ``log_name``."""
        process = subprocess.Popen(
            command,
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pump = threading.Thread(
            target=_timestamp_lines, args=(process.stdout, self.root / log_name), daemon=True
        )
        pump.start()
        self.pumps.append(pump)
        return process

    def close(self, show_logs=False):
        """Stop owned processes and remove scratch, keeping only redacted logs.

        The logs go to ``log_directory`` when set, and to stdout when ``show_logs``.
        """
        for process in (self.proxy, self.backend):
            if process is not None:
                _stop(process)
        for pump in self.pumps:
            pump.join(timeout=5)
        logs = {
            name: (self.root / name).read_bytes()
            for name in ("backend.log", "proxy.log", "nginx-error.log")
            if (self.root / name).is_file()
        }
        secrets = set().union(*map(harvest, logs.values()))
        logs = {name: scrub(data, secrets) for name, data in logs.items()}
        if show_logs:
            for name in ("proxy.log", "backend.log"):
                if name in logs:
                    print(logs[name].decode(errors="replace"))
        if self.log_directory is not None:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            for name, data in logs.items():
                (self.log_directory / (self.log_prefix + name)).write_bytes(data)
        shutil.rmtree(self.root, ignore_errors=True)

    def __exit__(self, exc_type, exc, traceback):
        self.close(show_logs=exc_type is not None)


def _timestamp_lines(stream, path):
    """Copy ``stream`` to ``path`` line by line, each prefixed with its UTC arrival time."""
    with stream, path.open("ab") as log:
        for line in stream:
            stamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            log.write(stamp.encode() + b" " + line)
            log.flush()


def smoke(f):
    prefix = f.prefix
    assert re.fullmatch(
        r"--ignore-certificate-errors-spki-list=[A-Za-z0-9+/]{43}=", f.chromium_certificate_arg()
    )
    status, headers, body = f.request(prefix + "/?fixture=a%2Fb")
    assert status == 200 and "no-store" in headers["Cache-Control"]
    html = body.decode()
    assert f'"basePath":"{prefix or "/"}"' in html
    assets = re.findall(r'(?:src|href)="\./([^"]+)"', html)
    assert assets
    for asset in assets:
        status, headers, body = f.request(
            prefix + "/" + asset + ("&" if "?" in asset else "?") + "v=fixture"
        )
        assert status == 200 and body
        if asset.endswith(".js"):
            assert (
                "javascript" in headers["Content-Type"] and "immutable" in headers["Cache-Control"]
            )
        if asset.endswith(".css"):
            assert "text/css" in headers["Content-Type"]
    if prefix:
        status, headers, _ = f.request(prefix + "?keep=a%2Fb")
        assert status in (307, 308) and headers["Location"] == prefix + "/?keep=a%2Fb"
        assert f.request("/api/auth/status")[0] == 404
        assert f.request(prefix + "other/api/auth/status")[0] == 404
    status, headers, body = f.request(
        prefix + "/api/auth/setup",
        method="POST",
        body={
            "username": "proxy-fixture-admin",
            "password": "isolated-proxy-fixture-password",
        },
        headers={"Origin": f.origin},
    )
    assert status == 201, (status, body)
    cookie = next(value for value in headers.get_all("Set-Cookie") if "Max-Age=0" not in value)
    assert "Secure" in cookie and "HttpOnly" in cookie and f"Path={prefix or '/'};" in cookie
    csrf = json.loads(body)["csrf_token"]
    authenticated = {"Cookie": cookie.split(";", 1)[0], "Origin": f.origin, "X-CSRF-Token": csrf}
    status, headers, _ = f.request(prefix + "/api/auth/status/?keep=a%2Fb", headers=authenticated)
    assert status in (307, 308) and headers["Location"] == prefix + "/api/auth/status?keep=a%2Fb"
    assert f.request(prefix + "/api/settings/deployment", headers=authenticated)[0] == 200
    assert (
        f.request(
            prefix + "/api/settings/deployment", method="PUT", body={}, headers=authenticated
        )[0]
        == 200
    )
    assert (
        f.request(
            prefix + "/api/settings/deployment",
            method="PUT",
            body={},
            headers={**authenticated, "Origin": "https://evil.example"},
        )[0]
        == 403
    )
    assert (
        f.request(
            prefix + "/api/settings/deployment",
            method="PUT",
            body={},
            headers={"Cookie": authenticated["Cookie"], "Origin": f.origin},
        )[0]
        == 403
    )
    # Neither a trusted-proxy hop nor a real loopback client replaces a session.
    assert f.request(prefix + "/api/settings/deployment")[0] == 401
    spoofs = {
        "Forwarded": "for=192.168.1.2",
        "X-Forwarded-For": "::1",
        "X-Real-IP": "::1",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Prefix": "/evil",
        "X-Forwarded-Proto": "http",
    }
    assert f.request(prefix + "/api/settings/deployment", headers=spoofs)[0] == 401
    assert f.request(prefix + "/api/settings/deployment", ipv6=True)[0] == 401
    assert f.request(prefix + "/api/settings/deployment", headers=spoofs, ipv6=True)[0] == 401
    status, headers, _ = f.request(prefix + "/api/auth/status", headers=authenticated)
    assert status == 200 and "no-store" in headers["Cache-Control"]
    for path in ("/api/not-a-route", "/assets/missing.js"):
        status, headers, _ = f.request(prefix + path, headers=authenticated)
        assert status == 404 and "text/html" not in headers.get("Content-Type", "")
    # Validate route/upstream replacement contract, keeping the same live backend.
    f.reconfigure(upstream_port=f.upstream_port, prefix=prefix or "/")
    f.wait_ready()
    return {
        "proxy": f.kind,
        "prefix": prefix or "/",
        "result": "passed",
        "assets": len(assets),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editarr", type=Path, required=True)
    parser.add_argument("--dependency-site-packages", type=Path, required=True)
    parser.add_argument("--proxy", choices=("nginx", "caddy", "both"), default="both")
    args = parser.parse_args()
    for kind in ("nginx", "caddy") if args.proxy == "both" else (args.proxy,):
        for prefix in ("/", "/editarr", "/media/editarr"):
            with ProxyFixture(kind, args.editarr, args.dependency_site_packages, prefix) as fixture:
                print(json.dumps(smoke(fixture)), flush=True)


if __name__ == "__main__":
    main()
