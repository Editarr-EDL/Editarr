"""Check that real Linux evidence fails closed when required work is missing."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import scanner.api as scanner_api
import scanner.security.auth_policy as auth_policy
from scanner.http import scan_jobs_api
from scanner.repository import update_scan_status
from scanner.settings.capabilities import (
    SUPPORTED_VIDEO_MODELS,
    CapabilityEnvironment,
    model_capabilities,
)
from scanner.settings.config import Settings
from scanner.settings.deployment_config import DeploymentSnapshot, parse_base_url
from scripts import jellyfin_stand_in
from scripts import verify_linux_models as smoke
from tests import test_jellyfin_stand_in

app = test_jellyfin_stand_in.app


def _capabilities():
    return {
        "target_id": "linux-x86_64",
        "video_models": [{"id": backend} for backend in smoke.DETECTORS],
        "model_capabilities": [
            {"id": backend, "availability": "available", "device": "cpu"}
            for backend in {"whisper", *smoke.DETECTORS, *smoke.FILTERS}
        ]
        + [
            {
                "id": "qwen3_vl",
                "availability": "unavailable",
                "reason": "deferred_gap",
                "enrichment_behavior": "detector_findings_only",
            }
        ],
    }


def test_real_matrix_covers_each_detector_and_audio_filter():
    cases = smoke.scan_cases(_capabilities())
    assert {case["video_model"] for case in cases if case["scan_video"]} == smoke.DETECTORS
    assert {case["audio_filter"] for case in cases if case["scan_audio"]} == smoke.FILTERS
    assert all(case["audio_model"] == "whisper" for case in cases if case["scan_audio"])


def test_real_matrix_matches_the_linux_package_detector_advertisement():
    environment = CapabilityEnvironment(
        system="Linux",
        machine="x86_64",
        package_available=lambda _package: True,
        providers=frozenset({"CPUExecutionProvider"}),
    )
    declared = {item.id: item for item in model_capabilities(environment)}
    advertised = {
        backend
        for backend in SUPPORTED_VIDEO_MODELS
        if declared[backend].availability == "available"
    }
    assert advertised == smoke.DETECTORS


@pytest.mark.parametrize("defect", ["unavailable", "cuda", "qwen", "extra_detector"])
def test_real_matrix_rejects_missing_cpu_or_untruthful_capabilities(defect):
    capabilities = _capabilities()
    if defect == "unavailable":
        capabilities["model_capabilities"][0]["availability"] = "unavailable"
    elif defect == "cuda":
        capabilities["model_capabilities"][0]["device"] = "cuda"
    elif defect == "qwen":
        capabilities["model_capabilities"][-1]["availability"] = "available"
    else:
        capabilities["video_models"].append({"id": "unverified-detector"})
    with pytest.raises(RuntimeError):
        smoke.scan_cases(capabilities)


@pytest.mark.parametrize("failure", ["failed", "test_fixture"])
def test_real_scan_never_accepts_failed_scan_or_fake_findings(monkeypatch, failure):
    responses = iter(
        [
            {"id": "scan"},
            [
                {
                    "id": "scan",
                    "status": "failed" if failure == "failed" else "complete",
                    "finding_count": 1,
                    "video_hash": "hash",
                    "error_message": "model loader failed",
                }
            ],
            {"findings": [{"source": "test_fixture"}]},
        ]
    )
    monkeypatch.setattr(smoke, "request", lambda *_args, **_kwargs: next(responses))
    with pytest.raises(RuntimeError, match="real scan failed|fake model adapter"):
        smoke.scan(
            "http://localhost",
            None,
            "csrf",
            {},
            SimpleNamespace(poll=lambda: None),
        )


def test_real_scan_reports_orphaned_worker_without_waiting_for_timeout(monkeypatch):
    responses = iter(
        [
            {"id": "scan"},
            [],
            [{"id": "scan", "status": "orphaned", "error_message": "model load failed"}],
        ]
    )
    monkeypatch.setattr(smoke, "request", lambda *_args, **_kwargs: next(responses))
    with pytest.raises(RuntimeError, match="model load failed"):
        smoke.scan(
            "http://localhost",
            None,
            "csrf",
            {},
            SimpleNamespace(poll=lambda: None),
        )


def test_verifier_sets_up_signs_in_and_scans_fresh_media_through_the_stand_in(
    app, tmp_path, monkeypatch
):
    media = tmp_path / "real fixture.mp4"
    app.include_router(scan_jobs_api.router)
    monkeypatch.setattr(scanner_api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(auth_policy, "get_session_factory", lambda: app.state.factory)
    origin = parse_base_url("http://testserver")
    app.state.deployment_snapshot = DeploymentSnapshot(
        base_url=origin.base_url,
        public_origin=origin.public_origin,
        prefix=origin.prefix,
        listening_port=80,
        base_url_source="saved",
        port_source="saved",
        cli_port_override=None,
        cookie_secure=False,
        session_not_before=None,
        legacy_cookie_paths=(),
    )
    app.add_middleware(auth_policy.AuthPolicyMiddleware, configuration=Settings(data_dir=tmp_path))
    with (
        jellyfin_stand_in.serve(media) as jellyfin,
        TestClient(app, headers={"Origin": "http://testserver"}) as client,
    ):

        def request(_origin, path, *, opener, csrf=None, data=None):
            headers = {"X-CSRF-Token": csrf} if csrf else {}
            reply = client.request("POST" if data else "GET", path, json=data, headers=headers)
            assert reply.status_code in {200, 201}, (path, reply.text)
            if path == "/api/scans":
                # Stand in for the worker, which this app does not start.
                with app.state.factory() as db:
                    update_scan_status(db, reply.json()["id"], "complete")
            return reply.json()

        monkeypatch.setattr(smoke, "request", request)
        running = SimpleNamespace(poll=lambda: None)
        smoke.sign_in("", None, jellyfin)
        client.cookies.clear()
        csrf = smoke.sign_in("", None, None)
        results = []
        for content in (b"first fixture", b"second fixture"):
            media.write_bytes(content)
            results.append(
                smoke.scan("", None, csrf, {"scan_audio": False, "scan_video": True}, running)
            )

    assert [result["status"] for result in results] == ["complete", "complete"]
    assert len({result["scan_id"] for result in results}) == 2
    assert None not in {result["video_hash"] for result in results}
    assert results[0]["video_hash"] != results[1]["video_hash"]
