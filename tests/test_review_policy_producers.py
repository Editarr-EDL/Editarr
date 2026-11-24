"""Every stored policy interval must retain its immutable source coverage."""

import ast
import copy
import json
import math
from fractions import Fraction
from pathlib import Path

import pytest

import scanner.api as root
from scanner import repository
from scanner.db import ReviewPolicyEvidenceRow, ScanRow
from scanner.formats.mcf_parser import parse_mcf_content
from scanner.formats.mcf_types import TimingInterval, map_affine_range
from scanner.http.review_policy_errors import install_review_policy_errors
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_policy_binding import body, import_review
from tests.test_review_policy_mapping import endpoints, exact_map
from tests.test_review_policy_saved_floor import app as app
from tests.test_review_policy_saved_floor import exports
from tests.test_review_policy_view import enforce


@pytest.mark.parametrize("width", [1.0, 0.01, 1e-6])
@pytest.mark.parametrize("end", [21602.0, 21600.002])
def test_imported_initial_binding_preserves_source_after_return(app, monkeypatch, width, end):
    duration = 43200.0
    original = (21600.001, end)
    window = (39839.51199209662, 39839.51199209662 + width)
    install_review_policy_errors(app.client.app)
    item_id = app.jellyfin_item(guid("review8-initial"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: duration)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: duration)
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    options = {
        "target": {"source": "jellyfin", "item_id": item_id},
        "name": "Initial binding",
        "synchronization": {
            "start_seconds": window[0],
            "end_seconds": window[1],
            "end_mode": "before_credits",
        },
        "mcf_material_end_seconds": window[1],
    }
    content = (
        b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Movie\nYEAR 2001\nTYPE movie\n\n"
        b"NOTE\nSTART 00:00:00.000\nEND 12:00:00.000\n\n"
        + f"06:00:00.001 --> 06:00:{end - 21600:06.3f}\nswearing=low=audio\n".encode()
    )
    app.client.cookies.clear()
    response = app.client.post(
        "/api/mcf-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={"options": json.dumps(options)},
        files={"file": ("input.mcf", content)},
    )
    assert response.status_code == 201, response.text
    detail = response.json()["review"]
    route = f"/api/reviews/{detail['id']}"
    with app.accounts.factory() as db:
        binding = db.query(ReviewPolicyEvidenceRow).filter_by(review_id=detail["id"]).one()
        for endpoint, required, lower in zip(
            (binding.start_seconds, binding.end_seconds), original, (True, False)
        ):
            restored = exact_map(endpoint, (binding.sync_start, binding.sync_end), (0, duration))
            assert restored <= Fraction(required) if lower else restored >= Fraction(required)
        media = Path(db.get(ScanRow, detail["memberships"][0]["scan_id"]).resolved_local_path)
    for value, required, lower in zip(endpoints(detail), original, (True, False)):
        restored = exact_map(value, window, (0, duration))
        assert restored <= Fraction(required) if lower else restored >= Fraction(required)
    _assert_export_coverage(exports(app, route, media, detail), window, (0, duration), original)
    mapped = map_affine_range(
        *endpoints(detail), TimingInterval(*window), TimingInterval(0.0, duration)
    )
    draft = copy.deepcopy(body(detail)) | {"filter_basis": "base"}
    draft["synchronization"].update(start_seconds=0.0, end_seconds=duration)
    draft["mcf_document"].update(material_start_seconds=0.0, material_end_seconds=duration)
    draft["filters"][0].update(start_seconds=mapped[0], end_seconds=mapped[1])
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 200, response.text
    fresh = app.send("GET", route, app.accounts.cookie(USER)).json()
    assert endpoints(fresh)[0] <= original[0]
    assert endpoints(fresh)[1] >= original[1]
    _assert_export_coverage(
        exports(app, route, media, fresh), (0, duration), (0, duration), original
    )


def _assert_export_coverage(content, synchronization, source, original):
    edl, mcf = content
    assert parse_mcf_content(mcf.encode()).entries
    edl_start, edl_end, _action = next(
        line for line in edl.splitlines() if line and not line.startswith("#")
    ).split()
    assert exact_map(Fraction(edl_start), synchronization, source) <= Fraction(original[0])
    assert exact_map(Fraction(edl_end), synchronization, source) >= Fraction(original[1])
    timing = next(line for line in mcf.splitlines() if " --> " in line)
    clocks = []
    for clock in timing.split(" --> "):
        hours, minutes, seconds = map(Fraction, clock.split(":"))
        clocks.append(hours * 3600 + minutes * 60 + seconds)
    assert exact_map(clocks[0], synchronization, source) <= Fraction(original[0])
    assert exact_map(clocks[1], synchronization, source) >= Fraction(original[1])


@pytest.mark.parametrize(
    "field,direction", [("start_seconds", math.inf), ("end_seconds", -math.inf)]
)
def test_source_backed_plain_roundoff_snaps_without_accumulating(
    app, monkeypatch, field, direction
):
    detail = import_review(
        app, monkeypatch, app.jellyfin_item(guid("source-one-ulp"), LIBRARY_A), b"1 2 1\n"
    )
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    route = f"/api/reviews/{detail['id']}"
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    floor = detail["filters"][0][field]
    for attempt in range(20):
        draft = copy.deepcopy(body(detail)) | {"filter_basis": "base"}
        draft["filters"][0][field] = math.nextafter(floor, direction)
        response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["base_filters"][0][field] == floor
    draft = copy.deepcopy(body(detail)) | {"filter_basis": "base"}
    draft["filters"][0][field] = floor + (1e-9 if direction > 0 else -1e-9)
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "review_policy_enforced"
    assert app.send("GET", route, app.accounts.cookie(USER)).json() == detail


def test_binding_writers_are_enumerated():
    root_path = Path(__file__).resolve().parents[1] / "scanner"
    producers = {
        name: set() for name in ("ReviewPolicyEvidenceRow", "ReviewFilterRow", "_map_policy_floor")
    }
    for path in root_path.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, ast.Name) and node.id == "_map_policy_floor":
                        producers[node.id].add((path.name, function.name))
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in producers
                    ):
                        producers[node.func.id].add((path.name, function.name))
    assert producers["ReviewPolicyEvidenceRow"] == {
        ("review_policy_evidence.py", "review_bindings"),
        ("review_policy_evidence.py", "copy_review_evidence"),
    }
    assert producers["ReviewFilterRow"] == {
        ("import_edl.py", "import_edl_as_saved_review"),
        ("repository_review_mutation.py", "duplicate_saved_review"),
        ("repository_review_core.py", "_snapshot_findings"),
        ("repository_review_core.py", "_filter_rows_from_inputs"),
        ("review_repairs.py", "recover_review"),
    }
    assert producers["_map_policy_floor"] == {
        ("import_mcf.py", "_import_mcf_as_saved_review"),
        ("repository_review_retime.py", "_map_retime_entries"),
        ("review_filter_tracking.py", "track_manual_edits"),
        ("review_policy_evidence.py", "evidence_interval"),
        ("review_policy.py", "validate_policy_draft"),
    }


def test_policy_interval_producers_use_source_basis_or_mapper():
    root_path = Path(__file__).resolve().parents[1] / "scanner/persistence/repository"
    paths = {
        ("repository_review_create.py", "_create_saved_review"): {"bind_review_evidence"},
        ("import_edl.py", "import_edl_as_saved_review"): {"bind_review_evidence"},
        ("import_mcf.py", "_import_mcf_as_saved_review"): {
            "bind_review_evidence",
            "_map_policy_floor",
        },
        ("repository_review_mutation.py", "_save_saved_review"): {
            "bind_review_evidence",
            "validate_policy_draft",
        },
        ("repository_review_mutation.py", "duplicate_saved_review"): {"copy_review_evidence"},
        ("repository_review_retime.py", "retime_saved_review_draft"): {"validate_policy_draft"},
        ("repository_review_retime.py", "_map_retime_entries"): {"mapper"},
        ("repository_scan_lifecycle.py", "accept_credits_detection"): {"prepare_review_policy"},
        ("review_policy_apply.py", "apply_review_defaults"): {"prepare_review_policy"},
        ("review_policy_evidence.py", "evidence_interval"): {"_map_policy_floor"},
        ("review_repairs.py", "recover_review"): {"bind_review_evidence"},
        ("review_policy.py", "validate_policy_draft"): {"_map_policy_floor", "required_evidence"},
        ("repository_review_operations.py", "export_saved_review_edl"): {"prepare_review_export"},
        ("repository_review_operations.py", "export_saved_review_mcf"): {
            "prepare_review_export",
        },
        ("review_export_content.py", "prepare_review_export"): {
            "prepare_review_policy",
            "require_policy_revision",
            "require_policy_evidence",
            "render_review_export",
        },
        ("review_export_content.py", "render_review_export"): {
            "policy_export_filters",
            "policy_export_interval",
        },
        ("export_retained.py", "prepare_retained_export"): {
            "owner_policy",
            "enforce_filter_policy",
            "evidence_interval",
            "snap_evidence",
            "covers_evidence",
            "render_review_export",
        },
        ("../../runtime/export_regeneration.py", "settle_regeneration"): {
            "current_job",
            "prepare_retained_export",
            "_replace",
            "_withdraw",
        },
        ("../../runtime/export_regeneration.py", "_replace"): {
            "recoverable_sidecar",
            "committed_export_content",
            "_stage_result",
        },
        ("../../runtime/export_regeneration.py", "_stage_result"): {
            "_audit",
            "save_snapshot",
        },
        ("export_publication.py", "publish_export"): {
            "prepare_review_export",
            "safe_destination",
            "_check_destination",
        },
    }
    for (filename, name), expected in paths.items():
        tree = ast.parse((root_path / filename).read_text())
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        calls = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert expected <= calls, (filename, name, expected - calls)
    evidence = (root_path / "review_policy_evidence.py").read_text()
    assert "map_affine_range" not in evidence
