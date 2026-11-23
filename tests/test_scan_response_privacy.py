"""Worker diagnostics cross the HTTP boundary only with path permission."""

from datetime import UTC, datetime

import pytest

from scanner.db import ScanRow
from scanner.http import scan_access
from scanner.http.scan_access import ScanCaller, SeenScan
from scanner.modeling.model_scans import ScanVerificationCoverage
from scanner.models import ScanQueueItem
from scanner.persistence.repository import review_access
from scanner.persistence.repository.repository_catalog import get_scan
from scanner.persistence.repository.scan_jobs import scan_job_facts
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import ALLOWED_MEDIA, OWNER, USER, build_access
from tests.scan_access_http import guid
from tests.test_scan_access_http import app as app


def test_copy_coverage_requires_access_to_original_findings(app, monkeypatch):
    source_id, _ = app.seed_scan(USER, "original", status="complete")
    copy_id, _ = app.seed_scan(USER, "copy", status="complete")
    with app.accounts.factory() as db:
        facts = scan_job_facts(db, [source_id, copy_id])
        source = SeenScan(facts[source_id], ALLOWED_MEDIA, job=True)
        copy = SeenScan(facts[copy_id], ALLOWED_MEDIA, job=True)
        coverage = ScanVerificationCoverage(
            source_counts={"audio": 1, "video": 0},
            covered_finding_ids={"audio": [1], "video": []},
        )
        scan = get_scan(db, copy_id).model_copy(
            update={
                "verified_from_scan_id": source_id,
                "verification_coverage": coverage,
            }
        )
        caller = ScanCaller(
            build_access(
                Role.USER,
                overrides={C.SCANS_FINDINGS_READ: OverrideEffect.DENY},
            ),
            db,
            datetime.now(UTC),
        )
        monkeypatch.setattr(caller, "seen", lambda _ids: {source_id: source})
        assert caller.present(scan, copy).verified_from_scan_id == source_id
        assert caller.present(scan, copy).verification_coverage is None


@pytest.mark.parametrize("review_response", [False, True])
def test_verification_source_authorization_is_batched(app, monkeypatch, review_response):
    source = app.seed_scan(USER, "batch-visible-original", status="complete")[0]
    missing = "missing-source"
    copies = [
        app.seed_scan(USER, f"batch-copy-{index}", status="complete")[0] for index in range(3)
    ]
    sources = [source, source, missing]
    coverage = ScanVerificationCoverage(
        source_counts={"audio": 1, "video": 0},
        covered_finding_ids={"audio": [1], "video": []},
    )
    with app.accounts.factory() as db:
        rows = [
            get_scan(db, copy).model_copy(
                update={
                    "verified_from_scan_id": source_id,
                    "verification_coverage": coverage,
                }
            )
            for copy, source_id in zip(copies, sources, strict=True)
        ]
        queries = []

        def captured_facts(session, ids):
            wanted = set(ids)
            queries.append(wanted)
            return scan_job_facts(session, wanted)

        access = build_access(Role.OWNER)
        if review_response:
            monkeypatch.setattr(review_access, "scan_job_facts", captured_facts)
            presented = review_access.ReviewAccess(db, access).present(rows)
            assert queries == [{source, missing}]
        else:
            monkeypatch.setattr(scan_access, "scan_job_facts", captured_facts)
            presented = ScanCaller(access, db, datetime.now(UTC)).visible(rows)
            assert queries == [{*copies, source, missing}]

        assert [scan.verified_from_scan_id for scan in presented] == [source, source, None]
        if not review_response:
            assert [scan.verification_coverage for scan in presented] == [coverage, coverage, None]


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("state", ["failed", "acquiring_model", "orphaned"])
def test_worker_diagnostics_are_path_safe_on_every_scan_projection(app, credential, state):
    message = (
        "Permission denied: /srv/private/model-cache/model.bin; "
        "temp=/private/tmp/worker/transcript.json; C:\\private\\model.bin; "
        "relative/cache/weights.bin"
    )
    scan_id, _ = app.seed_scan(USER, "private-diagnostics", status=state, error=message)
    with app.accounts.factory() as db:
        row = db.get(ScanRow, scan_id)
        row.progress_message = message
        row.progress_stage = "transcription"
        db.commit()
    routes = ["/api/scans/history"] if state == "failed" else ["/api/scans/queue"]
    headers = app.accounts.cookie(USER) if credential == "session" else app.accounts.key(USER)
    for route in routes:
        privileged = app.send("GET", route, app.accounts.key(OWNER))
        assert privileged.status_code == 200, privileged.text
        privileged_record = next(scan for scan in privileged.json() if scan["id"] == scan_id)
        assert privileged_record["error_message"] == message
        response = app.send("GET", route, headers)
        assert response.status_code == 200, response.text
        record = response.json()
        if isinstance(record, list):
            record = next(scan for scan in record if scan["id"] == scan_id)
        assert record["error_message"]
        for private in ("/srv/", "/private/", "C:\\private", "relative/cache"):
            assert private not in str(record)
    assert app.scan(scan_id).error_message == message


@pytest.mark.parametrize("credential", ["session", "key"])
def test_item_history_uses_the_same_safe_projection(app, credential):
    from scanner.http import scan_catalog_api

    app.client.app.include_router(scan_catalog_api.router)
    scan_id, _ = app.seed_scan(USER, "private-item-history", status="failed", error="/srv/secret")
    app.jellyfin_item(guid("private-item-history"), "a" * 32)
    headers = app.accounts.cookie(USER) if credential == "session" else app.accounts.key(USER)
    response = app.send("GET", f"/api/library-items/{guid('private-item-history')}/scans", headers)
    assert response.status_code == 200, response.text
    assert any(scan["id"] == scan_id for scan in response.json())
    assert "/srv/secret" not in response.text


@pytest.mark.parametrize(
    ("state", "stage", "expected"),
    [
        ("queued", "setup", "Scan queued"),
        ("orphaned", "transcription", "Scan interrupted"),
        ("scanning", "transcription", "Transcribing audio"),
        ("scanning", "/srv/private", "Scan in progress"),
    ],
)
def test_restricted_progress_describes_status_before_stage(app, state, stage, expected):
    scan_id, _ = app.seed_scan(USER, "safe-progress", status=state)
    with app.accounts.factory() as db:
        row = db.get(ScanRow, scan_id)
        row.progress_message = "/srv/private/worker.log"
        row.progress_stage = stage
        db.commit()
    response = app.send("GET", "/api/scans/queue", app.accounts.cookie(USER))
    assert response.status_code == 200, response.text
    record = next(scan for scan in response.json() if scan["id"] == scan_id)
    assert record["progress_message"] == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("awaiting_model_consent", "Awaiting model consent"),
        ("acquiring_model", "Acquiring scan model"),
        ("cancelling", "Cancelling scan"),
        ("cancelled", "Scan cancelled"),
        ("failed", "Scan could not complete"),
        ("error", "Scan could not complete"),
        ("complete", "Scan complete"),
    ],
)
def test_restricted_progress_preserves_waiting_and_terminal_status(app, state, expected):
    scan_id, _ = app.seed_scan(USER, "waiting-progress", status="queued")
    response = app.send("GET", "/api/scans/queue", app.accounts.cookie(USER))
    queued = ScanQueueItem.model_validate(
        next(scan for scan in response.json() if scan["id"] == scan_id)
    )
    scan = queued.model_copy(
        update={
            "status": state,
            "progress_stage": "transcription",
            "progress_message": "/srv/private/log",
        }
    )
    with app.accounts.factory() as db:
        seen = SeenScan(scan_job_facts(db, [scan_id])[scan_id], ALLOWED_MEDIA, job=True)
        member = ScanCaller(build_access(Role.USER), db, datetime.now(UTC))
        owner = ScanCaller(build_access(Role.OWNER), db, datetime.now(UTC))
        assert member.present(scan, seen).progress_message == expected
        assert owner.present(scan, seen).progress_message == "/srv/private/log"
    assert scan.progress_message == "/srv/private/log"
