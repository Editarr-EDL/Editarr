"""The local replay UI exercises the web API and the existing report comparison."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import MagicMock
from urllib.parse import quote
from wave import open as open_wave

import pytest
from fastapi.testclient import TestClient

from scanner.evaluation.qwen_language_replay import LanguageReplayReport
from scanner.http import qwen_language_replay_process
from scanner.http.qwen_language_replay_web import create_app
from scanner.models import FindingCreate
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL, QWEN35_9B_MODEL, SUPPORTED_QWEN_MODELS

_REPO = Path(__file__).resolve().parents[1]
_ORIGIN = "http://127.0.0.1:8767"
_LOCAL = {"Origin": _ORIGIN}


def _client(directory, **options):
    return TestClient(
        create_app(repo_dir=_REPO, session_dir=directory, **options),
        base_url=_ORIGIN,
        client=("127.0.0.1", 53000),
    )


def _audio_bytes(sample=b"\0\0", *, frames=320):
    output = BytesIO()
    with open_wave(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(sample * frames)
    return output.getvalue()


def _upload(client, *, name="excerpt one.wav", content=None, headers=None):
    return client.put(
        "/api/audio",
        headers={
            **_LOCAL,
            "Content-Type": "application/octet-stream",
            "X-Audio-Name": quote(name, safe=""),
            **(headers or {}),
        },
        content=_audio_bytes() if content is None else content,
    )


def _select_audio_path(client, path, *, headers=_LOCAL):
    return client.put("/api/audio-path", headers=headers, json={"path": str(path)})


def _start(client, slot, **options):
    return client.post("/api/run", headers=_LOCAL, json={"slot": slot, **options})


def _finished(client):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get("/api/status")
        assert response.is_success
        state = response.json()
        if state["active"] is None:
            return state
        sleep(0.01)
    raise AssertionError("Replay job did not finish")


def _write_report(output, request, fingerprint, findings):
    report = LanguageReplayReport(
        audio_sha256=fingerprint,
        transcript_sha256="0" * 64,
        audio_model=request.audio_model,
        transcription_model="whisper@revision",
        qwen_model=request.qwen_model,
        qwen_revision="revision",
        audio_padding_seconds=request.audio_padding_seconds,
        deny_words=request.deny_words,
        status="complete",
        findings=findings,
    )
    output.write_text(report.model_dump_json(), encoding="utf-8")


def test_web_replay_compares_two_runs_of_the_same_upload(tmp_path):
    calls = []

    def run(audio, output, request, progress):
        calls.append((audio, audio.read_bytes(), request))
        fingerprint = sha256(audio.read_bytes()).hexdigest()
        progress("80% Using cached transcript")

        def finding(label, start, end):
            return FindingCreate(
                video_id=fingerprint,
                category="language",
                subcategory="nameCalling",
                label=label,
                start_seconds=start,
                end_seconds=end,
                severity="low" if request.slot == "baseline" else "high",
                source="audio",
            )

        findings = [finding("jerk", 1.1, 1.6)]
        if request.slot == "baseline":
            findings.extend(
                [
                    finding("jerk", 9.0, 9.7),
                    finding("you fool", 3.0, 3.8),
                    finding("drat", 6.0, 6.5),
                ]
            )
        else:
            findings.extend(
                [finding("darn", 7.0, 7.5), finding("jerk", 9.1, 9.7), finding("fool", 3.2, 3.8)]
            )
        _write_report(output, request, fingerprint, findings)

    options = {
        "audio_model": "whisper",
        "qwen_model": "qwen3-vl-4b-instruct-4bit",
        "audio_padding_seconds": 0.2,
        "deny_words": ["jerk"],
    }

    with _client(tmp_path, runner=run) as client:
        page = client.get("/")
        assert page.is_success and "Qwen" in page.text
        assert page.headers["Cache-Control"] == "no-store"
        for url, media_type in (("/replay.js", "text/javascript"), ("/replay.css", "text/css")):
            asset = client.get(url)
            assert asset.is_success and asset.headers["Content-Type"].startswith(media_type)
        initial = client.get("/api/status").json()
        assert initial["audio"] is None
        assert initial["reports"] == {"baseline": None, "candidate": None}
        assert QWEN35_9B_MODEL not in SUPPORTED_QWEN_MODELS
        assert initial["options"]["qwen_models"] == [*SUPPORTED_QWEN_MODELS, QWEN35_9B_MODEL]
        assert initial["options"]["qwen_model"] == DEFAULT_QWEN_MODEL
        assert initial["options"]["upload_limit_bytes"] >= 253_662_314
        assert _start(client, "baseline").is_client_error

        upload = _upload(client)
        assert upload.is_success
        assert upload.json()["audio"] == {
            "name": "excerpt one.wav",
            "size_bytes": len(_audio_bytes()),
        }
        assert _start(client, "baseline", **options).status_code == 202
        baseline = _finished(client)
        assert baseline["reports"]["baseline"]["findings"][0]["severity"] == "low"
        assert baseline["reports"]["candidate"] is None
        assert client.get("/api/compare").is_client_error

        candidate_options = {**options, "qwen_model": QWEN35_9B_MODEL}
        assert _start(client, "candidate", **candidate_options).status_code == 202
        candidate = _finished(client)
        assert candidate["reports"]["candidate"]["findings"][0]["severity"] == "high"
        assert candidate["error"] is None
        comparison = client.get("/api/compare")
        assert comparison.is_success
        result = comparison.json()
        diff = result["diff"]
        assert (
            f"Setting changed (qwen_model): {options['qwen_model']!r} → {QWEN35_9B_MODEL!r}" in diff
        )
        assert '-1.100s–1.600s  "jerk"  language/nameCalling  low' in diff
        assert '+1.100s–1.600s  "jerk"  language/nameCalling  high' in diff
        assert (result["baseline_count"], result["candidate_count"]) == (4, 4)
        assert any(note.startswith("Setting changed (qwen_model)") for note in result["notes"])
        rows = result["rows"]
        timestamps = [row["timestamp_seconds"] for row in rows]
        assert timestamps == sorted(timestamps)
        assert timestamps == pytest.approx([1.1, 3.0, 6.0, 7.0, 9.0])
        assert [
            tuple(
                row[slot]["label"] if row[slot] is not None else None
                for slot in ("baseline", "candidate")
            )
            for row in rows
        ] == [
            ("jerk", "jerk"),
            ("you fool", "fool"),
            ("drat", None),
            (None, "darn"),
            ("jerk", "jerk"),
        ]
        saved = client.get("/api/reports/baseline")
        assert saved.is_success
        assert saved.headers["Content-Disposition"].startswith("attachment;")
        assert saved.json()["audio_sha256"] == sha256(_audio_bytes()).hexdigest()
        assert saved.json()["deny_words"] == ["jerk"]
        assert client.get("/api/reports/candidate").json()["qwen_model"] == QWEN35_9B_MODEL

        assert client.get("/").is_success
        assert client.get("/api/status").json() == candidate
        assert client.get("/api/compare").json() == result

    with _client(tmp_path, runner=run) as client:
        reopened = client.get("/api/status").json()
        assert reopened["audio"] == candidate["audio"]
        assert reopened["reports"] == candidate["reports"]
        assert client.get("/api/compare").json() == result
        assert client.get("/api/reports/baseline").json() == saved.json()

        replacement = _upload(client, name="second.wav", content=_audio_bytes(b"\1\0"))
        assert replacement.json()["reports"] == {"baseline": None, "candidate": None}
        assert client.get("/api/reports/baseline").is_client_error

    with _client(tmp_path, runner=run) as client:
        assert client.get("/api/status").json()["reports"] == {"baseline": None, "candidate": None}

    assert [request.slot for _, _, request in calls] == ["baseline", "candidate"]
    assert [request.qwen_model for _, _, request in calls] == [
        options["qwen_model"],
        QWEN35_9B_MODEL,
    ]
    assert calls[0][:2] == calls[1][:2]
    assert calls[0][0].is_relative_to(tmp_path)


def test_web_replay_uses_local_audio_without_upload_or_copy(tmp_path):
    content = _audio_bytes()
    original = tmp_path / "cached clip.wav"
    original.write_bytes(content)
    session = tmp_path / "session"
    calls = []

    def run(audio, output, request, _progress):
        calls.append(audio)
        _write_report(output, request, sha256(audio.read_bytes()).hexdigest(), [])

    with _client(session, runner=run, upload_limit=len(content) - 1) as client:
        assert _upload(client, content=content).status_code == 413
        selected = _select_audio_path(client, original)
        assert selected.is_success
        assert selected.json()["audio"] == {
            "name": "cached clip.wav",
            "size_bytes": len(content),
            "source": "local",
        }
        assert not list(session.rglob("*.audio"))

        for slot in ("baseline", "candidate"):
            assert _start(client, slot, audio_padding_seconds=0.2).status_code == 202
            completed = _finished(client)
            assert completed["reports"][slot]["status"] == "complete"
        assert client.get("/api/compare").is_success

        directory = tmp_path / "directory.wav"
        directory.mkdir()
        unsupported = tmp_path / "unsupported.txt"
        unsupported.write_bytes(content)
        for invalid in ("relative.wav", tmp_path / "missing.wav", directory, unsupported):
            assert _select_audio_path(client, invalid).status_code == 400
            assert client.get("/api/status").json() == completed

    unavailable = tmp_path / "temporarily moved.wav"
    original.rename(unavailable)
    with _client(session, runner=run, upload_limit=len(content) - 1) as client:
        reopened = client.get("/api/status").json()
        assert reopened["reports"] == completed["reports"]
        assert client.get("/api/compare").is_success
        unavailable.rename(original)

        replacement = _upload(client, name="second.wav", content=_audio_bytes(frames=20))
        assert replacement.is_success
        assert replacement.json()["reports"] == {"baseline": None, "candidate": None}
        assert client.get("/api/reports/baseline").is_client_error

    assert calls == [original.resolve(), original.resolve()]
    assert original.read_bytes() == content


def test_web_replay_serializes_jobs_and_reports_failure(tmp_path):
    running = Event()
    resume = Event()

    def fail(_audio, _output, _request, progress):
        progress("Transcribing audio")
        running.set()
        assert resume.wait(timeout=5)
        raise RuntimeError("Qwen could not load")

    with _client(tmp_path, runner=fail) as client:
        original = tmp_path / "cached clip.wav"
        original.write_bytes(_audio_bytes())
        assert _upload(client).is_success
        try:
            assert _start(client, "baseline").status_code == 202
            assert running.wait(timeout=5)
            assert client.get("/api/status").json()["active"] == {
                "slot": "baseline",
                "message": "Transcribing audio",
            }
            assert _start(client, "candidate").status_code == 409
            assert _upload(client).status_code == 409
            assert _select_audio_path(client, original).status_code == 409
        finally:
            resume.set()

        state = _finished(client)
        assert state["reports"]["baseline"] is None
        assert state["error"]["slot"] == "baseline"
        assert "Qwen could not load" in state["error"]["message"]
        assert client.get("/api/compare").is_client_error


def test_web_replay_refuses_cross_origin_and_untrusted_paths(tmp_path):
    with _client(tmp_path) as client:
        original = tmp_path / "cached clip.wav"
        original.write_bytes(_audio_bytes())
        assert client.get("/api/status", headers={"Host": "external.example:8767"}).is_client_error
        assert client.post("/api/run", json={"slot": "baseline"}).is_client_error
        assert _upload(client, headers={"Origin": "http://external.example"}).is_client_error
        assert _select_audio_path(client, original, headers={}).status_code == 403
        assert (
            _select_audio_path(
                client,
                original,
                headers={
                    "Origin": "http://external.example",
                },
            ).status_code
            == 403
        )
        assert client.get("/api/status").json()["audio"] is None

        assert _upload(client).is_success
        assert _start(client, "../../outside").is_client_error
        assert _start(client, "baseline", output="/tmp/outside.json").is_client_error
        assert _start(client, "baseline", audio_model="unsupported-model").is_client_error
        assert client.get("/api/reports/not-a-slot").is_client_error


def test_web_replay_limits_uploads_and_preserves_the_current_audio(tmp_path):
    content = _audio_bytes()
    with _client(tmp_path, upload_limit=len(content)) as client:
        assert client.get("/api/status").json()["options"]["upload_limit_bytes"] == len(content)
        assert _upload(client, content=content).is_success
        assert _upload(client, name="empty.wav", content=b"").is_client_error
        assert _upload(client, name="too-big.wav", content=content + b"0").status_code == 413
        streamed = iter([content[:32], content[32:], b"0"])
        assert _upload(client, name="too-big.wav", content=streamed).status_code == 413
        audio = client.get("/api/status").json()["audio"]
        assert audio == {"name": "excerpt one.wav", "size_bytes": len(content)}


def test_browser_runner_starts_current_checkout_in_a_fresh_python_process(tmp_path, monkeypatch):
    process = MagicMock()
    process.stdout = iter(["80% Using cached transcript\n", "Saved 1 finding\n"])
    process.wait.return_value = 0
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(qwen_language_replay_process.subprocess, "Popen", popen)
    updates = []
    request = qwen_language_replay_process.RunRequest(
        slot="candidate",
        qwen_model=QWEN35_9B_MODEL,
        audio_padding_seconds=0.25,
        deny_words=["--audio-model=whisper"],
    )
    cached = tmp_path / "transcripts"
    runner = qwen_language_replay_process.ReplayProcess(_REPO, cached)
    runner(tmp_path / "clip.wav", tmp_path / "report.json", request, updates.append)

    (argv,), kwargs = popen.call_args
    assert kwargs["cwd"] == _REPO
    assert not kwargs.get("shell", False)
    assert argv[argv.index("-m") + 1 :][:3] == [
        "scanner.commands.qwen_language_replay_cli",
        "run",
        str(tmp_path / "clip.wav"),
    ]
    assert "-B" in argv
    assert argv[argv.index("-X") + 1].startswith("pycache_prefix=")
    assert argv[argv.index("--cache-dir") + 1] == str(cached)
    assert argv[argv.index("--qwen-model") + 1] == QWEN35_9B_MODEL
    assert "--deny-word=--audio-model=whisper" in argv
    assert updates == ["80% Using cached transcript"]
