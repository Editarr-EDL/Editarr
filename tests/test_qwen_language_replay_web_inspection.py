"""Exercise decision inspection through the loopback replay API."""

import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from scanner.evaluation.qwen_language_inspection import (
    InspectionAnswer,
    InspectionRerun,
    InspectionTestEvidence,
)
from scanner.evaluation.qwen_language_replay import LanguageReplayReport
from scanner.http import qwen_language_replay_process
from scanner.http.qwen_language_replay_web import create_app
from scanner.models import FindingCreate
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL

_REPO = Path(__file__).resolve().parents[1]
_ORIGIN = "http://127.0.0.1:8767"
_HEADERS = {"Origin": _ORIGIN}


def _wait_for_replay(client):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        state = client.get("/api/status").json()
        if state["active"] is None:
            assert state["error"] is None
            return
        sleep(0.01)
    raise AssertionError("Replay job did not finish")


def test_inspect_both_runs_and_rerun_saved_batch_without_replacing_reports(tmp_path):
    audio = b"test audio"
    fingerprint = sha256(audio).hexdigest()
    segments = [
        {
            "text": "You fool",
            "words": [
                {"word": "You", "start_seconds": 1.0, "end_seconds": 1.2},
                {"word": " fool", "start_seconds": 1.2, "end_seconds": 1.7},
            ],
        }
    ]
    transcript = json.dumps(segments).encode()
    cache = tmp_path / "transcripts"
    transcript_dir = cache / "v1" / fingerprint
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "faster-whisper-base-revision.json").write_bytes(transcript)
    finding = FindingCreate(
        video_id=fingerprint,
        category="language",
        subcategory="nameCalling",
        label="fool",
        start_seconds=1.1,
        end_seconds=1.8,
        severity="low",
        source="audio",
    )

    def run(_audio, output, request, _progress):
        report = LanguageReplayReport(
            audio_sha256=fingerprint,
            transcript_sha256=sha256(transcript).hexdigest(),
            audio_model=request.audio_model,
            transcription_model="faster-whisper-base@revision",
            qwen_model=request.qwen_model,
            qwen_revision=None,
            audio_padding_seconds=0.1,
            deny_words=[],
            status="complete",
            findings=[finding] if request.slot == "baseline" else [],
        )
        output.write_text(report.model_dump_json(exclude={"decision_trace"}), encoding="utf-8")

    inspection_started, resume_inspection = Event(), Event()
    calls = []

    def inspect(action, evidence, text, _work_dir, *, latest_test=None):
        calls.append((action, evidence.flagged, text, evidence.input_json, latest_test))
        if action == "ask":
            if text == "Fail the model request.":
                raise RuntimeError("Qwen could not start.")
            inspection_started.set()
            assert resume_inspection.wait(timeout=5)
            return InspectionAnswer(
                answer="The phrase may have been judged literally.",
                suggested_edit="Flag 'fool' when directed at another person as an insult.",
            )
        assert latest_test is None
        if text == "An instruction that returns invalid JSON.":
            return InspectionRerun(accepted=False, response="invalid JSON", findings=[])
        return InspectionRerun(accepted=True, response="{}", findings=[finding])

    app = create_app(
        repo_dir=_REPO,
        session_dir=tmp_path / "session",
        cache_dir=cache,
        runner=run,
        inspection_runner=inspect,
    )
    target = {"start_seconds": 1.1, "end_seconds": 1.8, "label": "fool"}
    with TestClient(app, base_url=_ORIGIN) as client:
        assert client.put("/api/audio", headers=_HEADERS, content=audio).is_success
        for slot in ("baseline", "candidate"):
            assert client.post("/api/run", headers=_HEADERS, json={"slot": slot}).status_code == 202
            _wait_for_replay(client)
        outputs = {
            slot: client.get(f"/api/reports/{slot}").content for slot in ("baseline", "candidate")
        }
        baseline = client.post(
            "/api/inspect/context", headers=_HEADERS, json={**target, "slot": "baseline"}
        ).json()
        missed = {**target, "slot": "candidate"}
        assert client.post("/api/inspect/context", json=missed).status_code == 403
        candidate = client.post("/api/inspect/context", headers=_HEADERS, json=missed).json()
        assert baseline["flagged"] and not candidate["flagged"]
        assert baseline["evidence"] == candidate["evidence"] == "reconstructed"
        assert baseline["finding"]["label"] == "fool" and candidate["finding"] is None
        assert candidate["accepted"] is None and candidate["response"] is None
        assert candidate["latest_rerun"] is None
        assert baseline["inputs"] == candidate["inputs"]
        assert baseline["context_id"] != candidate["context_id"]

        payload = {**missed, "context_id": candidate["context_id"]}
        assert (
            client.post(
                "/api/inspect/ask",
                headers=_HEADERS,
                json={**payload, "context_id": baseline["context_id"], "question": "Why?"},
            ).status_code
            == 409
        )
        failure = client.post(
            "/api/inspect/ask",
            headers=_HEADERS,
            json={**payload, "question": "Fail the model request."},
        )
        assert failure.status_code == 502 and "Qwen could not start" in failure.json()["detail"]

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending_answer = executor.submit(
                client.post,
                "/api/inspect/ask",
                headers=_HEADERS,
                json={**payload, "rerun_id": None, "question": "Why was this missed?"},
            )
            try:
                assert inspection_started.wait(timeout=5)
                assert (
                    client.post("/api/run", headers=_HEADERS, json={"slot": "baseline"}).status_code
                    == 409
                )
                assert (
                    client.put("/api/audio", headers=_HEADERS, content=b"else").status_code == 409
                )
            finally:
                resume_inspection.set()
            answer = pending_answer.result(timeout=5).json()
        assert calls[-1][-1] is None

        edited = f"{candidate['instructions']}\n\n{answer['suggested_edit']}  "
        trial = client.post(
            "/api/inspect/rerun", headers=_HEADERS, json={**payload, "instructions": edited}
        )
        assert trial.is_success
        trial = trial.json()
        assert trial["findings"][0]["label"] == "fool"
        assert trial["instructions"] == edited
        assert calls[-1] == ("rerun", False, edited, calls[-2][3], None)
        reopened = client.post("/api/inspect/context", headers=_HEADERS, json=missed).json()
        assert reopened["latest_rerun"] == trial

        follow_up = {
            **payload,
            "rerun_id": trial["rerun_id"],
            "question": "Why did the test flag it?",
        }
        assert client.post("/api/inspect/ask", headers=_HEADERS, json=follow_up).is_success
        assert calls[-1] == (
            "ask",
            False,
            follow_up["question"],
            calls[-2][3],
            InspectionTestEvidence(
                instructions=edited,
                result=InspectionRerun(accepted=True, response="{}", findings=[finding]),
            ),
        )
        test_evidence = calls[-1][-1]
        assert client.post(
            "/api/inspect/ask",
            headers=_HEADERS,
            json={**payload, "question": "Why did the latest test flag it?"},
        ).is_success
        assert calls[-1][-1] == test_evidence
        assert (
            client.post(
                "/api/inspect/ask",
                headers=_HEADERS,
                json={**payload, "rerun_id": None, "question": "Why did the saved run miss it?"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/inspect/ask",
                headers=_HEADERS,
                json={**follow_up, "slot": "baseline", "context_id": baseline["context_id"]},
            ).status_code
            == 409
        )

        invalid_instructions = "An instruction that returns invalid JSON."
        rejected = client.post(
            "/api/inspect/rerun",
            headers=_HEADERS,
            json={**payload, "instructions": invalid_instructions},
        ).json()
        assert rejected["rerun_id"] != trial["rerun_id"]
        assert client.post("/api/inspect/ask", headers=_HEADERS, json=follow_up).status_code == 409
        assert client.post(
            "/api/inspect/ask",
            headers=_HEADERS,
            json={**follow_up, "rerun_id": rejected["rerun_id"], "question": "Why did it fail?"},
        ).is_success
        assert calls[-1][-1] == InspectionTestEvidence(
            instructions=invalid_instructions,
            result=InspectionRerun(accepted=False, response="invalid JSON", findings=[]),
        )

        assert all(
            client.get(f"/api/reports/{slot}").content == raw for slot, raw in outputs.items()
        )

        assert (
            client.post("/api/run", headers=_HEADERS, json={"slot": "candidate"}).status_code == 202
        )
        _wait_for_replay(client)
        assert (
            client.post("/api/inspect/context", headers=_HEADERS, json=missed).json()[
                "latest_rerun"
            ]
            is None
        )
        assert (
            client.post(
                "/api/inspect/ask",
                headers=_HEADERS,
                json={**follow_up, "rerun_id": rejected["rerun_id"]},
            ).status_code
            == 409
        )


def test_model_inspection_uses_private_stdin_not_command_arguments(tmp_path, monkeypatch):
    process = MagicMock()
    process.returncode = 0
    process.communicate.return_value = ('{"answer":"Resolved.","suggested_edit":null}', "")
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(qwen_language_replay_process.subprocess, "Popen", popen)
    evidence = MagicMock()
    evidence.model_dump.return_value = {"qwen_model": DEFAULT_QWEN_MODEL}
    latest = InspectionTestEvidence(
        instructions="A prompt edit.",
        result=InspectionRerun(accepted=False, response="invalid JSON", findings=[]),
    )

    result = qwen_language_replay_process.ReplayProcess(_REPO, None).inspect(
        "ask", evidence, "Why did this happen?", tmp_path, latest_test=latest
    )

    (argv,), options = popen.call_args
    assert argv[-2:] == ["-m", "scanner.commands.qwen_language_inspect"]
    assert not options.get("shell", False)
    assert options["cwd"] == _REPO
    assert options["stdin"] == options["stderr"] == qwen_language_replay_process.subprocess.PIPE
    assert json.loads(process.communicate.call_args.args[0]) == {
        "action": "ask",
        "evidence": {"qwen_model": DEFAULT_QWEN_MODEL},
        "text": "Why did this happen?",
        "latest_test": latest.model_dump(mode="json"),
    }
    assert result.answer == "Resolved."
