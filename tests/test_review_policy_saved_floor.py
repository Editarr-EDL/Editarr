"""Saved-only enforcement has no renewable floating-point narrowing budget."""

import copy
import math
import random
from fractions import Fraction
from pathlib import Path

import pytest

from scanner import repository
from scanner.db import MediaItemExportRow, ScanRow
from scanner.formats.export_layout import parse_export_id, personal_export_path
from scanner.models import ExportFormat
from scanner.persistence.rows.db_policies import ExportScope
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app
from tests.test_review_policy_binding import body, import_review, tokens
from tests.test_review_policy_view import enforce


def saved_only_review(app, monkeypatch):
    detail = import_review(
        app, monkeypatch, app.jellyfin_item(guid("saved-floor"), LIBRARY_A), b"1.00049 2.00051 1\n"
    )
    route = f"/api/reviews/{detail['id']}"
    data = body(detail)
    data["filters"][0].update(
        label="Edited match",
        category="language",
        subcategory="swearing",
        severity="low",
        mcf_channel="audio",
    )
    data["mcf_document"].update(
        title="Movie",
        year=2001,
        work_type="movie",
        material_start_seconds=0,
        material_end_seconds=50,
    )
    saved = app.send("PUT", route, app.accounts.unsafe_cookie(USER), data)
    assert saved.status_code == 200, saved.text
    with app.accounts.factory() as db:
        media = Path(db.get(ScanRow, detail["memberships"][0]["scan_id"]).resolved_local_path)
    enforce(app.accounts, rules=[rule("deny", label_contains="Edited match")])
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    assert detail["filters"][0]["selected"]
    return route, media, detail


def exports(app, route, media, detail):
    content = []
    for export_format in ("edl", "mcf"):
        response = app.send(
            "POST", route + "/" + export_format, app.accounts.unsafe_cookie(USER), tokens(detail)
        )
        assert response.status_code == 200, response.text
        with app.accounts.factory() as db:
            row = (
                db.query(MediaItemExportRow)
                .filter_by(
                    review_id=detail["id"],
                    format=ExportFormat(export_format),
                    scope=ExportScope.PERSONAL,
                    target_user_id=USER,
                )
                .one()
            )
            destination = personal_export_path(
                media, parse_export_id(row.target_export_id), ExportFormat(export_format)
            )
            assert Path(row.destination_path) == destination
            assert destination.read_bytes() == row.content_bytes
            content.append(destination.read_text())
    return tuple(content)


def assert_outward_mapping(actual, original, source, target):
    """Independently require the tightest covering floats for an exact affine image."""
    source_start, source_end = map(Fraction, source)
    target_start, target_end = map(Fraction, target)
    for endpoint, value, direction in zip(original, actual, (math.inf, -math.inf)):
        exact = target_start + (Fraction(endpoint) - source_start) * (target_end - target_start) / (
            source_end - source_start
        )
        if direction > 0:
            assert Fraction(value) <= exact < Fraction(math.nextafter(value, direction))
        else:
            assert Fraction(math.nextafter(value, direction)) < exact <= Fraction(value)


def test_http_saved_metadata_floor_cannot_erode_across_saves(app, monkeypatch):
    route, media, detail = saved_only_review(app, monkeypatch)
    before = exports(app, route, media, detail)
    statuses = []
    for attempt in range(30):
        data = body(detail)
        data["filters"][0]["start_seconds"] += 0.75e-6
        data["filters"][0]["end_seconds"] -= 0.75e-6
        response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), data)
        statuses.append(response.status_code)
        assert response.status_code in {200, 409}, response.text
        detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    after = exports(app, route, media, detail)
    print({"statuses": statuses, "before": before, "after": after})
    assert detail["filters"][0]["start_seconds"] <= 1.00049
    assert detail["filters"][0]["end_seconds"] >= 2.00051
    assert after == before


@pytest.mark.parametrize("seed", range(5))
def test_random_sub_tolerance_saves_never_move_exported_endpoints_inward(app, monkeypatch, seed):
    route, media, detail = saved_only_review(app, monkeypatch)
    before = exports(app, route, media, detail)
    generator = random.Random(seed)
    for attempt in range(40):
        data = body(detail)
        data["filters"][0]["start_seconds"] += generator.uniform(0.1e-6, 0.99e-6)
        data["filters"][0]["end_seconds"] -= generator.uniform(0.1e-6, 0.99e-6)
        response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), data)
        assert response.status_code in {200, 409}, response.text
        detail = app.send("GET", route, app.accounts.cookie(USER)).json()
        assert exports(app, route, media, detail) == before


@pytest.mark.parametrize("widening", [0, 0.01])
def test_saved_metadata_floor_allows_repeated_product_retimes_and_widening(
    app, monkeypatch, widening
):
    route, media, detail = saved_only_review(app, monkeypatch)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 60.0)
    for start, end in [(0.125, 58.875), (0.375, 59.125), (0.25, 57.75), (0, 60)] * 5:
        data = body(detail)
        data["filters"][0]["start_seconds"] -= widening
        data["filters"][0]["end_seconds"] += widening
        widened = app.send("PUT", route, app.accounts.unsafe_cookie(USER), data)
        assert widened.status_code == 200, widened.text
        detail = widened.json()
        data = body(detail)
        data.pop("name")
        synchronization = data.pop("synchronization")
        data.update(
            current_synchronization=synchronization,
            proposed_synchronization=synchronization | {"start_seconds": start, "end_seconds": end},
            new_manual_filters=[],
        )
        response = app.send("POST", route + "/retime", app.accounts.unsafe_cookie(USER), data)
        assert response.status_code == 200, response.text
        result = response.json()
        saved = app.send(
            "PUT",
            route,
            app.accounts.unsafe_cookie(USER),
            body(detail)
            | {key: result[key] for key in ("synchronization", "mcf_document", "accepted_credits")}
            | {"filters": result["base_filters"], "filter_basis": "base"},
        )
        assert saved.status_code == 200, saved.text
        detail = saved.json()
    assert not detail["policy_missing_evidence"]
    exports(app, route, media, detail)


@pytest.mark.parametrize(
    "start,end,duration",
    [(0.001, 2.0, 60.0), (0.01, 2.0, 60.0), (21600.001, 21602.0, 43200.0)],
)
@pytest.mark.parametrize("seed", range(24))
def test_near_zero_and_hour_scale_there_and_back(app, monkeypatch, start, end, duration, seed):
    route, media, detail = saved_only_review(app, monkeypatch)
    enforce(app.accounts, rules=[])
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    with app.accounts.factory() as db:
        db.get(ScanRow, detail["memberships"][0]["scan_id"]).duration_seconds = duration
        db.commit()
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: duration)
    draft = copy.deepcopy(body(detail))
    draft["filters"][0].update(start_seconds=start, end_seconds=end, selected=True)
    draft["synchronization"].update(start_seconds=0, end_seconds=duration)
    draft["mcf_document"].update(material_end_seconds=duration * 0.8)
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 200, response.text
    enforce(app.accounts, rules=[rule("deny", label_contains="Edited match")])
    original = app.send("GET", route, app.accounts.cookie(USER)).json()
    before = exports(app, route, media, original)
    draft = copy.deepcopy(body(original))
    generator = random.Random(seed + 100)
    for _step in range(10):
        target = (
            round(generator.uniform(0, 0.9), 3),
            round(duration * generator.uniform(0.95, 0.99), 3),
        )
        draft = _retime_draft(app, route, draft, *target)
        assert_outward_mapping(
            tuple(draft["filters"][0][field] for field in ("start_seconds", "end_seconds")),
            (start, end),
            (0, duration),
            target,
        )
    draft = _retime_draft(app, route, draft, 0, duration)
    assert draft["filters"][0] == body(original)["filters"][0]
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 200, response.text
    fresh = app.send("GET", route, app.accounts.cookie(USER)).json()
    assert fresh["filters"] == original["filters"]
    assert exports(app, route, media, fresh) == before


def _retime_draft(app, route, draft, start, end):
    data = copy.deepcopy(draft)
    data.pop("name", None)
    synchronization = data.pop("synchronization")
    data.update(
        current_synchronization=synchronization,
        proposed_synchronization=synchronization | {"start_seconds": start, "end_seconds": end},
        new_manual_filters=[],
        filter_basis="base",
    )
    response = app.send("POST", route + "/retime", app.accounts.unsafe_cookie(USER), data)
    assert response.status_code == 200, response.text
    result = response.json()
    return (
        draft
        | {key: result[key] for key in ("synchronization", "mcf_document", "accepted_credits")}
        | {"filters": result["base_filters"], "filter_basis": "base"}
    )


@pytest.mark.parametrize("seed", range(8))
def test_chained_unsaved_retimes_then_one_save_are_accepted(app, monkeypatch, seed):
    route, media, detail = saved_only_review(app, monkeypatch)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 60.0)
    generator = random.Random(seed)
    draft = copy.deepcopy(body(detail))
    for _ in range(3):
        draft = _retime_draft(
            app,
            route,
            draft,
            round(generator.uniform(0, 0.9), 3),
            round(generator.uniform(57.1, 59.9), 3),
        )
    stored = detail["synchronization"]
    target = draft["synchronization"]
    original = detail["filters"][0]
    saved = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert saved.status_code == 200, saved.text
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    value = detail["filters"][0]
    assert_outward_mapping(
        (value["start_seconds"], value["end_seconds"]),
        (original["start_seconds"], original["end_seconds"]),
        (stored["start_seconds"], stored["end_seconds"]),
        (target["start_seconds"], target["end_seconds"]),
    )
    assert not detail["policy_missing_evidence"]
    exports(app, route, media, detail)


@pytest.mark.parametrize("seed", range(8))
def test_retime_there_and_back_without_saving_is_accepted(app, monkeypatch, seed):
    route, media, detail = saved_only_review(app, monkeypatch)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 60.0)
    original = copy.deepcopy(body(detail))
    before = exports(app, route, media, detail)
    generator = random.Random(100 + seed)
    draft = _retime_draft(
        app,
        route,
        original,
        round(generator.uniform(0, 0.9), 3),
        round(generator.uniform(51, 59.9), 3),
    )
    sync = original["synchronization"]
    draft = _retime_draft(app, route, draft, sync["start_seconds"], sync["end_seconds"])
    assert draft["filters"][0] == original["filters"][0]
    saved = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert saved.status_code == 200, saved.text
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    value = detail["filters"][0]
    assert value["start_seconds"] == original["filters"][0]["start_seconds"]
    assert value["end_seconds"] == original["filters"][0]["end_seconds"]
    assert exports(app, route, media, detail) == before


@pytest.mark.parametrize(
    "field,direction", [("start_seconds", math.inf), ("end_seconds", -math.inf)]
)
def test_plain_one_ulp_narrowing_is_refused(app, monkeypatch, field, direction):
    route, media, detail = saved_only_review(app, monkeypatch)
    before = exports(app, route, media, detail)
    data = copy.deepcopy(body(detail))
    data["filters"][0][field] = math.nextafter(data["filters"][0][field], direction)
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), data)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "review_policy_enforced"
    assert exports(app, route, media, detail) == before


@pytest.mark.parametrize("steps", [-4, -1, 1, 4, 4096])
def test_changed_sync_roundoff_is_snapped_before_storage(app, monkeypatch, steps):
    route, media, detail = saved_only_review(app, monkeypatch)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 60.0)
    for start, end in [(0.3, 58.7), (0.1, 59.1), (0.375, 58.875)] * 3:
        draft = _retime_draft(app, route, copy.deepcopy(body(detail)), start, end)
        expected = copy.deepcopy(draft["filters"][0])
        for field, direction in [("start_seconds", math.inf), ("end_seconds", -math.inf)]:
            for _ in range(abs(steps)):
                draft["filters"][0][field] = math.nextafter(
                    draft["filters"][0][field], direction if steps > 0 else -direction
                )
        response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
        assert response.status_code == (409 if steps == 4096 else 200), response.text
        fresh = app.send("GET", route, app.accounts.cookie(USER)).json()
        if steps == 4096:
            assert fresh["filters"] == detail["filters"]
        else:
            value = fresh["filters"][0]
            assert value["start_seconds"] == expected["start_seconds"]
            assert value["end_seconds"] == expected["end_seconds"]
        detail = fresh
    exports(app, route, media, detail)


def test_affine_roundoff_bound_stays_below_one_nanosecond_for_twelve_hours():
    from scanner.persistence.repository.review_policy import _affine_roundoff_bound

    minute_bound = _affine_roundoff_bound(0.001, 0, 60, 0.131, 58.092)
    assert 65 * math.ulp(0.001) < minute_bound < 1e-12
    twelve_hour_bound = _affine_roundoff_bound(21600.001, 0, 43200, 0.131, 41800)
    assert minute_bound < twelve_hour_bound < 1e-9
