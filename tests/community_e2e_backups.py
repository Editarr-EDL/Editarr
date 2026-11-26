"""Real backup export and initial-setup restore, plain and encrypted, into empty installations."""

import sqlite3
import time
from threading import Event


def wait_job(application, path, headers=None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = application.client.get(path, headers=headers)
        if response.status_code == 503 and response.json().get("code") == "database_maintenance":
            Event().wait(0.025)
            continue
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["state"] != "failed", job
        if job["state"] in {"ready", "activated"}:
            return job
        Event().wait(0.025)
    raise AssertionError(f"Backup job never settled: {path}")


def backup_roundtrips(configuration, reader, link, installation_type):
    """Restore the exact registered identity and master links with their snapshot bytes."""
    source_profile = reader.request("GET", "/api/community/connection")
    for encrypted in (False, True):
        passphrase = "synthetic backup passphrase" if encrypted else None
        exported = reader.request(
            "POST",
            "/api/backups/jobs",
            {
                "action": "export",
                "encrypt": encrypted,
                "passphrase": passphrase,
            },
        )
        export_path = f"/api/backups/jobs/{exported['id']}"
        wait_job(reader, export_path)
        response = reader.client.get(export_path + "/download")
        assert response.status_code == 200, response.text
        archive = response.content
        assert archive.startswith(b"age-encryption.org/v1\n") is encrypted
        restored = installation_type(
            reader.directory.parent / f"restored-full-{encrypted}", configuration["local"]
        )
        try:
            flow = restored.request("POST", "/api/setup-restore/flow")
            headers = {"Origin": restored.origin, "X-Setup-Restore-Token": flow["token"]}
            job_response = restored.client.post("/api/setup-restore/jobs", headers=headers)
            assert job_response.status_code == 200, job_response.text
            restore_path = f"/api/setup-restore/jobs/{job_response.json()['id']}"
            uploaded = restored.client.put(
                restore_path + "/archive", headers=headers, content=archive
            )
            assert uploaded.status_code == 200, uploaded.text
            preflight = restored.client.post(
                restore_path + "/preflight", headers=headers, json={"passphrase": passphrase}
            )
            assert preflight.status_code == 200, preflight.text
            plan = wait_job(restored, restore_path, headers)
            assert plan["plan"]["kind"] == "full"
            activated = restored.client.post(
                restore_path + "/activate",
                headers=headers,
                json={"confirmed": True},
            )
            assert activated.status_code == 200, activated.text
            deadline = time.monotonic() + 30
            hold = restored.directory / "restore-reconciliation.json"
            while not hold.exists() and time.monotonic() < deadline:
                Event().wait(0.025)
            assert hold.is_file(), "Restore did not durably commit"
            restored.restart()
            remote = restored.request("GET", "/api/community/connection")
            assert remote["identity"]["keyId"] == source_profile["identity"]["keyId"]
            assert remote["profile"]["contributorId"] == source_profile["profile"]["contributorId"]
            with sqlite3.connect(restored.directory / "scanner.db") as db:
                sql = (
                    "SELECT l.id, l.community_version_id, s.revision, s.snapshot_bytes, "
                    "s.receipt_json FROM community_master_links l JOIN community_master_snapshots "
                    "s ON s.link_id = l.id ORDER BY l.id, s.revision"
                )
                rows = db.execute(sql).fetchall()
                assert [row[0] for row in rows] == [link["linkId"]]
                with sqlite3.connect(reader.directory / "scanner.db") as original:
                    assert rows == original.execute(sql).fetchall()
            refused = restored.client.post(
                "/api/setup-restore/flow", headers={"Origin": restored.origin}
            )
            assert refused.status_code in {403, 409}, refused.text
        finally:
            restored.close()
        discarded = reader.request("DELETE", export_path)
        assert discarded["state"] == "cancelled", discarded
