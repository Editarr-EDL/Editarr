"""The proxy fixture keeps timestamped backend and proxy logs as run evidence (w17)."""

import importlib
import io
import re
import sys

import pytest

STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z ")
CREDENTIALS = {
    "X-CSRF-Token": "pSsyWIC1uAnajISq6_kzNwGZ0Uv1vVsdg31Wss_qAtw",
    "X-Api-Key": "edk_synthetic-api-key-0123456789",
    "Authorization": "synthetic-bearer-token-0123456789",
    "password": "planted-fixture-password",
    "api_key": "edk_synthetic-json-key-0123456789",
}
KEPT = re.compile(r"^\d{8}T\d{6}Z-primary-(backend|proxy|nginx-error)\.log$")


@pytest.fixture
def proxy_fixture(monkeypatch):
    monkeypatch.syspath_prepend("scripts")
    module = importlib.import_module("proxy_fixture")
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/true")
    return module


def _emit(*lines):
    source = (
        "import sys, time\n"
        "for line in sys.argv[1:]:\n"
        "    print(line, flush=True)\n"
        "    time.sleep(0.05)"
    )
    return [sys.executable, "-c", source, *lines]


def test_close_keeps_timestamped_backend_and_proxy_logs(proxy_fixture, tmp_path):
    evidence = tmp_path / "evidence"
    fixture = proxy_fixture.ProxyFixture(
        "nginx", tmp_path / "editarr", None, log_directory=evidence, label="primary"
    )
    fixture.backend = fixture._spawn(
        _emit("started", "PUT /api/settings/deployment 200"), "backend.log"
    )
    fixture.proxy = fixture._spawn(_emit("proxy ready"), "proxy.log")
    (fixture.root / "nginx-error.log").write_text("2026/11/01 05:00:00 [notice] start\n")
    fixture.backend.wait(10)
    fixture.proxy.wait(10)
    root = fixture.root

    fixture.close()

    assert not root.exists()
    kept = {KEPT.match(path.name).group(1): path for path in evidence.iterdir()}
    assert set(kept) == {"backend", "proxy", "nginx-error"}
    backend = kept["backend"].read_text().splitlines()
    assert [STAMP.sub("", line) for line in backend] == [
        "started",
        "PUT /api/settings/deployment 200",
    ]
    assert all(STAMP.match(line) for line in backend)
    assert STAMP.sub("", kept["proxy"].read_text()) == "proxy ready\n"
    assert kept["nginx-error"].read_text() == "2026/11/01 05:00:00 [notice] start\n"


def test_close_without_an_evidence_directory_keeps_nothing(proxy_fixture, tmp_path):
    fixture = proxy_fixture.ProxyFixture("nginx", tmp_path / "editarr", None)
    fixture.backend = fixture._spawn(_emit("started"), "backend.log")
    fixture.backend.wait(10)
    root = fixture.root

    fixture.close()

    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


def test_timestamped_credentials_are_found_and_redacted(proxy_fixture, tmp_path):
    secrets_module = importlib.import_module("evidence_secrets")
    lines = [
        f"X-CSRF-Token: {CREDENTIALS['X-CSRF-Token']}\n",
        f"x-api-key: {CREDENTIALS['X-Api-Key']}\n",
        f"Authorization: Bearer {CREDENTIALS['Authorization']}\n",
        f'{{"password": "{CREDENTIALS["password"]}", "api_key": "{CREDENTIALS["api_key"]}"}}\n',
    ]
    log = tmp_path / "evidence" / "backend.log"
    log.parent.mkdir()
    proxy_fixture._timestamp_lines(io.BytesIO("".join(lines).encode()), log)
    assert all(STAMP.match(line) for line in log.read_text().splitlines())

    found = secrets_module.known_secrets([log.parent], [])
    assert {value.encode() for value in CREDENTIALS.values()} <= found
    assert secrets_module.redact([log.parent], found) == [str(log)]

    kept = log.read_text()
    assert not [name for name, value in CREDENTIALS.items() if value in kept]
    assert kept.count("[REDACTED]") == len(CREDENTIALS)
    assert secrets_module.scan([log.parent], found) == []


def test_close_publishes_only_redacted_logs(proxy_fixture, tmp_path):
    evidence = tmp_path / "evidence"
    fixture = proxy_fixture.ProxyFixture(
        "nginx", tmp_path / "editarr", None, log_directory=evidence, label="primary"
    )
    fixture.backend = fixture._spawn(
        _emit(
            f"X-CSRF-Token: {CREDENTIALS['X-CSRF-Token']}",
            f'{{"password": "{CREDENTIALS["password"]}"}}',
        ),
        "backend.log",
    )
    fixture.backend.wait(10)

    fixture.close()

    (kept,) = evidence.iterdir()
    text = kept.read_text()
    assert [STAMP.sub("", line) for line in text.splitlines()] == [
        "X-CSRF-Token: [REDACTED]",
        '{"password": "[REDACTED]"}',
    ]


def test_a_failed_run_prints_only_redacted_logs(proxy_fixture, tmp_path, capsys):
    fixture = proxy_fixture.ProxyFixture("nginx", tmp_path / "editarr", None)
    fixture.backend = fixture._spawn(
        _emit(
            "backend failed",
            f"X-CSRF-Token: {CREDENTIALS['X-CSRF-Token']}",
            f'{{"password": "{CREDENTIALS["password"]}"}}',
        ),
        "backend.log",
    )
    fixture.backend.wait(10)

    fixture.__exit__(KeyboardInterrupt, KeyboardInterrupt(), None)

    printed = capsys.readouterr().out
    assert "backend failed" in printed
    assert CREDENTIALS["X-CSRF-Token"] not in printed
    assert CREDENTIALS["password"] not in printed
    assert printed.count("[REDACTED]") == 2
