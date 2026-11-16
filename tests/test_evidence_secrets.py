"""Evidence scans look inside archives and binary files for credentials (MU-45 fix 2)."""

import io
import json
import zipfile

from scripts import evidence_secrets

PASSWORD = "planted-fixture-password"
SESSION = "Apj_oje-6NvJt2wa8uLzqHLFy8mQi_XSKjgCOYWS2Kw"
CSRF = "pSsyWIC1uAnajISq6_kzNwGZ0Uv1vVsdg31Wss_qAtw"


def _zip(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return output.getvalue()


def _trace():
    network = json.dumps(
        {
            "request": {
                "headers": [
                    {"name": "Cookie", "value": f"editarr_session={SESSION}"},
                    {"name": "x-csrf-token", "value": CSRF},
                ],
                "postData": json.dumps({"username": "owner", "password": PASSWORD}),
            }
        }
    )
    return _zip(
        {
            "0-trace.network": network,
            # A typed password and a token repeated without their labels.
            "0-trace.trace": json.dumps({"method": "fill", "value": PASSWORD, "seen": CSRF}),
            "resources/page.json": json.dumps({"csrf_token": CSRF, "state": "ready"}),
        }
    )


def _run(*arguments, capsys):
    status = evidence_secrets.main([str(argument) for argument in arguments])
    return status, json.loads(capsys.readouterr().out)


def _members(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_a_secret_inside_a_trace_zip_fails_the_scan(tmp_path, capsys):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "trace.zip").write_bytes(_trace())
    (tmp_path / "run" / "summary.txt").write_text("passed\n")

    status, report = _run(tmp_path, capsys=capsys)

    assert status == 1
    locations = {finding["location"] for finding in report["remaining"]}
    trace = str(tmp_path / "run" / "trace.zip")
    assert locations == {
        f"{trace}!0-trace.network",
        f"{trace}!0-trace.trace",
        f"{trace}!resources/page.json",
    }
    assert PASSWORD not in json.dumps(report)


def test_a_known_secret_in_a_nested_archive_and_a_binary_file_is_found(tmp_path, capsys):
    secret = "only-the-caller-knows-this"
    (tmp_path / "bundle.zip").write_bytes(_zip({"inner/trace.zip": _zip({"note.bin": secret})}))
    (tmp_path / "frame.bin").write_bytes(b"\x89\xff\x00" + secret.encode() + b"\x00\xfe")

    status, report = _run(tmp_path, "--secret", secret, capsys=capsys)

    assert status == 1
    assert {finding["location"] for finding in report["remaining"]} == {
        f"{tmp_path / 'bundle.zip'}!inner/trace.zip!note.bin",
        str(tmp_path / "frame.bin"),
    }


def test_redaction_scrubs_archive_members_and_keeps_them_readable(tmp_path, capsys):
    trace = tmp_path / "trace.zip"
    trace.write_bytes(_trace())

    status, report = _run(tmp_path, "--redact", capsys=capsys)

    assert status == 0
    assert report["redacted"] == [str(trace)]
    assert report["remaining"] == []
    members = _members(trace)
    assert set(members) == {"0-trace.network", "0-trace.trace", "resources/page.json"}
    everything = b"".join(members.values())
    for secret in (PASSWORD, SESSION, CSRF):
        assert secret.encode() not in everything
    assert json.loads(members["resources/page.json"]) == {
        "csrf_token": "[REDACTED]",
        "state": "ready",
    }
    assert _run(tmp_path, capsys=capsys)[0] == 0


def test_clean_evidence_passes(tmp_path, capsys):
    (tmp_path / "trace.zip").write_bytes(_zip({"0-trace.trace": '{"csrf_token": "invalid"}'}))

    status, report = _run(tmp_path, capsys=capsys)

    assert (status, report["remaining"]) == (0, [])
