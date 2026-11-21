#!/usr/bin/env python3
"""Run real Linux package scans in disposable state, then reuse weights offline.

Invoke as ``python -m scripts.verify_linux_models``. Only the standard-library
black-box verifier is imported; all model code runs inside the packaged application.
Generated colour/tone media proves execution, not detector accuracy or speech quality.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

from scripts import verify_installed_release as verifier
from scripts.jellyfin_stand_in import ADMIN, ADMIN_PASSWORD, MOVIE_ID
from scripts.jellyfin_stand_in import serve as serve_jellyfin

TARGET = "linux-x86_64"
DETECTORS = {
    "nudenet",
    "image_safety_gate",
    "siglip2_moderation_nsfw",
    "siglip2_moderation_violence",
    "d1-3b",
}
FILTERS = {"better_profanity", "glin_profanity", "alt_profanity_check"}


def scan_cases(capabilities: dict) -> list[dict]:
    """Fail on missing baseline backends instead of silently reducing the matrix."""
    models = {item["id"]: item for item in capabilities["model_capabilities"]}
    if capabilities["target_id"] != TARGET:
        raise RuntimeError("real Linux smoke requires a Linux package")
    for backend in {"whisper", *DETECTORS, *FILTERS}:
        if models[backend]["availability"] != "available" or models[backend]["device"] != "cpu":
            raise RuntimeError(f"required CPU backend is not available: {backend}")
    qwen = models["qwen3_vl"]
    if (qwen["availability"], qwen["reason"], qwen["enrichment_behavior"]) != (
        "unavailable",
        "deferred_gap",
        "detector_findings_only",
    ):
        raise RuntimeError("Linux Qwen disclosure does not match the release contract")
    if {item["id"] for item in capabilities["video_models"]} != DETECTORS:
        raise RuntimeError("update the real model smoke for the advertised detector matrix")
    return [
        {
            "scan_audio": True,
            "scan_video": False,
            "audio_model": "whisper",
            "audio_filter": audio_filter,
        }
        for audio_filter in sorted(FILTERS)
    ] + [
        {"scan_audio": False, "scan_video": True, "video_model": detector}
        for detector in sorted(DETECTORS)
    ]


def request(origin: str, path: str, *, opener, csrf=None, data=None):
    status, _headers, body = verifier._request(
        origin + path, opener=opener, csrf=csrf, data=data, timeout=30
    )
    if status not in {200, 201, 202}:
        raise RuntimeError(f"{path} returned HTTP {status}: {verifier._redact(body.decode())}")
    return json.loads(body)


def sign_in(origin: str, opener, jellyfin: str | None) -> str:
    """Set up as the stand-in's administrator when given its URL, else sign in as it."""
    credentials = {"username": ADMIN, "password": ADMIN_PASSWORD}
    if jellyfin:
        path, data = "/api/auth/setup", {"server_url": jellyfin, **credentials}
    else:
        path, data = "/api/auth/jellyfin/login", credentials
    return request(origin, path, opener=opener, data=data)["csrf_token"]


def scan(origin: str, opener, csrf: str, case: dict, process) -> dict:
    """Queue the stand-in's movie with `case` and wait for its real-model result."""
    queued = request(
        origin,
        "/api/scans",
        opener=opener,
        csrf=csrf,
        data={"jellyfin_item_id": MOVIE_ID, **case},
    )
    started = time.monotonic()
    while time.monotonic() - started < 2400:
        if process.poll() is not None:
            raise RuntimeError("packaged controller exited during real inference")
        history = request(origin, "/api/scans/history", opener=opener)
        result = next((item for item in history if item["id"] == queued["id"]), None)
        if result and result["status"] in {"complete", "error", "failed", "cancelled"}:
            if result["status"] != "complete":
                raise RuntimeError(f"real scan failed: {result.get('error_message')}")
            findings = request(origin, f"/api/scans/{queued['id']}/findings", opener=opener)
            if any(item.get("source") == "test_fixture" for item in findings["findings"]):
                raise RuntimeError("real inference was replaced by a fake model adapter")
            return {
                "case": case,
                "scan_id": queued["id"],
                "status": "complete",
                "seconds": round(time.monotonic() - started, 3),
                "finding_count": result["finding_count"],
                "video_hash": result["video_hash"],
            }
        queue = request(origin, "/api/scans/queue", opener=opener)
        failed = next(
            (
                item
                for item in queue
                if item["id"] == queued["id"]
                and item["status"] in {"error", "failed", "orphaned", "cancelled"}
            ),
            None,
        )
        if failed:
            raise RuntimeError(f"real scan failed: {failed.get('error_message')}")
        time.sleep(1)
    raise RuntimeError("real model scan exceeded the 40-minute bound")


def write_evidence(path: Path, evidence: dict) -> None:
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args) -> int:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("run this verifier on native Linux x86_64")
    runtime = args.runtime.resolve()
    metadata = verifier._runtime_inventory(runtime, TARGET)
    executable = args.appimage.resolve() if args.appimage else verifier._executable(runtime, TARGET)
    root = Path(tempfile.mkdtemp(prefix="editarr-real-linux-"))
    evidence = {
        "schema": "editarr-linux-real-models-v1",
        "target": TARGET,
        "artifact_sha256": verifier._sha256(executable),
        "runtime_inventory_sha256": metadata["inventory_sha256"],
        "source_revision": metadata["source_revision"],
        "host": platform.platform(),
        "glibc": platform.libc_ver(),
        "launch_mode": args.mode,
        "phases": {},
        "failures": [],
    }
    before = verifier._program_snapshot(runtime)
    data = root / "data"
    data.mkdir()
    port = verifier._available_port()
    (data / "deployment.json").write_text(json.dumps({"version": 1, "port": port}))
    environment = verifier._environment(runtime, None, data, native=True)
    environment["CUDA_VISIBLE_DEVICES"] = ""
    environment["OMP_NUM_THREADS"] = "2"
    environment["OPENBLAS_NUM_THREADS"] = "2"
    if args.appimage and args.mode == "extract":
        environment["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    else:
        environment.pop("APPIMAGE_EXTRACT_AND_RUN", None)
    # Every scan reads the stand-in's one movie; each regenerates it with new content.
    media = root / "real fixture.mp4"
    try:
        with serve_jellyfin(media) as jellyfin:
            for phase in ("online", "offline"):
                if phase == "offline":
                    # urllib (the managed downloader) and HF clients cannot fetch weights.
                    # Loopback, the stand-in included, still answers control requests.
                    environment.update(
                        {
                            "HF_HUB_OFFLINE": "1",
                            "TRANSFORMERS_OFFLINE": "1",
                            "http_proxy": "http://127.0.0.1:9",
                            "https_proxy": "http://127.0.0.1:9",
                            "HTTP_PROXY": "http://127.0.0.1:9",
                            "HTTPS_PROXY": "http://127.0.0.1:9",
                            "NO_PROXY": "localhost,127.0.0.1",
                            "no_proxy": "localhost,127.0.0.1",
                        }
                    )
                process, _log = verifier._start(executable, None, root, environment)
                try:
                    origin = f"http://localhost:{port}"
                    verifier._wait_ready(origin, process)
                    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    csrf = sign_in(origin, opener, jellyfin if phase == "online" else None)
                    capabilities = request(origin, "/api/capabilities", opener=opener)
                    cases = scan_cases(capabilities)
                    phase_evidence = {"capabilities": capabilities, "scans": []}
                    evidence["phases"][phase] = phase_evidence
                    for index, case in enumerate(cases):
                        # Each scan has different content, including after relaunch, so
                        # neither audio nor visual result caches can substitute for inference.
                        sequence = index + (len(cases) if phase == "offline" else 0)
                        subprocess.run(
                            [
                                str(verifier._bundled_helper(runtime, "ffmpeg", TARGET)),
                                "-f",
                                "lavfi",
                                "-i",
                                f"color=c=0x{0x446600 + sequence * 100:06x}:s=320x240:r=1",
                                "-f",
                                "lavfi",
                                "-i",
                                f"sine=frequency={300 + sequence * 20}:sample_rate=16000",
                                # The stand-in reports a one-second movie.
                                "-t",
                                "1",
                                "-pix_fmt",
                                "yuv420p",
                                "-y",
                                str(media),
                            ],
                            check=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                            timeout=30,
                        )
                        print(f"{phase}: {case}", flush=True)
                        phase_evidence["scans"].append(scan(origin, opener, csrf, case, process))
                        write_evidence(args.json_out, evidence)
                    phase_evidence["model_storage"] = request(origin, "/api/models", opener=opener)
                    owners = {
                        process.pid,
                        *(pid for pid, _parent, _command in verifier._descendant_rows(process.pid)),
                    }
                    phase_evidence["quit"] = verifier._request_native_quit(data, owners)
                    process.wait(timeout=30)
                    if process.returncode != 0:
                        raise RuntimeError(f"package Quit returned {process.returncode}")
                finally:
                    verifier._stop(process)
        hashes = [
            item["video_hash"]
            for phase in ("online", "offline")
            for item in evidence["phases"][phase]["scans"]
        ]
        if None in hashes or len(set(hashes)) != len(hashes):
            raise RuntimeError("fixture hashes repeated, so caches could replace inference")
        evidence["runtime_immutable"] = before == verifier._program_snapshot(runtime)
        if not evidence["runtime_immutable"]:
            raise RuntimeError("real model scans modified the installed runtime")
        return 0
    except Exception as error:
        evidence["failures"].append(verifier._redact(str(error))[:4000])
        # Preserve only bounded, redacted application diagnostics, never credentials.
        evidence["diagnostics"] = {
            path.name: verifier._redact(path.read_text(errors="replace")[-4000:])
            for path in (root / "home" / ".local" / "state" / "editarr").glob("*.log")
        }
        evidence["launcher_diagnostics"] = {
            path.name: verifier._redact(path.read_text(errors="replace")[-4000:])
            for path in sorted(root.glob("Editarr-*.log"))[-2:]
        }
        return 1
    finally:
        write_evidence(args.json_out, evidence)
        shutil.rmtree(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--appimage", type=Path)
    parser.add_argument("--mode", choices=("fuse", "extract"), default="extract")
    parser.add_argument("--json-out", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
