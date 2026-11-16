"""Synchronization changes must preserve policy floors in their original timeline."""

import copy
import math
import random
from fractions import Fraction

import pytest

from scanner import repository
from scanner.db import ScanRow
from scanner.formats.mcf_types import TimingInterval, map_affine_range
from scanner.persistence.repository.review_policy import (
    ReviewPolicyConflict,
    _affine_roundoff_bound,
    _map_policy_floor,
)
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.test_review_policy_binding import body
from tests.test_review_policy_saved_floor import app as app
from tests.test_review_policy_saved_floor import exports, saved_only_review
from tests.test_review_policy_view import enforce


def setup_range(app, monkeypatch, start, end, duration):
    route, media, detail = saved_only_review(app, monkeypatch)
    enforce(app.accounts, rules=[])
    detail = app.send("GET", route, app.accounts.cookie(USER)).json()
    with app.accounts.factory() as db:
        for membership in detail["memberships"]:
            db.get(ScanRow, membership["scan_id"]).duration_seconds = duration
        db.commit()
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: duration)
    draft = copy.deepcopy(body(detail))
    draft["filters"][0].update(start_seconds=start, end_seconds=end, selected=True)
    draft["synchronization"].update(start_seconds=0, end_seconds=duration)
    draft["mcf_document"].update(material_start_seconds=0, material_end_seconds=duration * 0.8)
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 200, response.text
    enforce(app.accounts, rules=[rule("deny", label_contains="Edited match")])
    return route, media, app.send("GET", route, app.accounts.cookie(USER)).json()


def endpoints(detail):
    return tuple(detail["filters"][0][field] for field in ("start_seconds", "end_seconds"))


def synchronization(detail):
    return tuple(detail["synchronization"][field] for field in ("start_seconds", "end_seconds"))


def exact_map(value, source, target):
    source_start, source_end = map(Fraction, source)
    target_start, target_end = map(Fraction, target)
    return target_start + (Fraction(value) - source_start) * (target_end - target_start) / (
        source_end - source_start
    )


def save_mapping(app, route, detail, target):
    values = map_affine_range(
        *endpoints(detail), TimingInterval(*synchronization(detail)), TimingInterval(*target)
    )
    draft = copy.deepcopy(body(detail)) | {"filter_basis": "base"}
    draft["synchronization"].update(zip(("start_seconds", "end_seconds"), target))
    draft["mcf_document"].update(material_start_seconds=target[0], material_end_seconds=target[1])
    draft["filters"][0].update(zip(("start_seconds", "end_seconds"), values))
    response = app.send("PUT", route, app.accounts.unsafe_cookie(USER), draft)
    assert response.status_code == 200, response.text
    fresh = app.send("GET", route, app.accounts.cookie(USER)).json()
    assert fresh == response.json()
    return fresh


@pytest.mark.parametrize(
    "start,end,duration,window",
    [
        (0.001, 2.0, 60.0, (30.034284579431706, 30.034284580431706)),
        (0.001, 2.0, 60.0, (55.33265554574976, 55.332655545751756)),
        (21600.001, 21602.0, 43200.0, (39839.51199209662, 39839.51199309662)),
    ],
)
def test_compressed_round_trips_narrow_the_saved_floor(
    app, monkeypatch, start, end, duration, window
):
    route, media, detail = setup_range(app, monkeypatch, start, end, duration)
    exports(app, route, media, detail)
    original = tuple(map(Fraction, endpoints(detail)))
    full = (0.0, duration)
    for target in (window, full):
        detail = save_mapping(app, route, detail, target)
        restored = tuple(exact_map(value, target, full) for value in endpoints(detail))
        assert restored[0] <= original[0]
        assert restored[1] >= original[1]
    exports(app, route, media, detail)


def replay_server_save(floor, current, target, duration):
    """Replay one save's server floor mapping and roundoff snap with the production helpers."""
    if target == current:
        return floor
    source, destination = TimingInterval(*current), TimingInterval(*target)
    submitted = map_affine_range(*floor, source, destination)
    required = _map_policy_floor(*floor, source, destination, duration_seconds=duration)
    stored = tuple(
        required_value
        if abs(value - required_value)
        <= _affine_roundoff_bound(base, value, required_value, *current, *target)
        else value
        for base, value, required_value in zip(floor, submitted, required)
    )
    assert stored[0] <= required[0] and stored[1] >= required[1]
    return stored


HTTP_HOPS = 50


@pytest.mark.parametrize("start,end,duration", [(0.001, 2.0, 60.0), (21600.001, 21602.0, 43200.0)])
def test_thousands_of_server_saves_have_zero_inward_movement(
    app, monkeypatch, start, end, duration
):
    """Check 2000 adversarial hops; the first HTTP_HOPS must match the live server exactly."""
    route, _media, detail = setup_range(app, monkeypatch, start, end, duration)
    generator = random.Random(3)
    full = (0.0, duration)
    original = tuple(map(Fraction, endpoints(detail)))
    floor, current = endpoints(detail), synchronization(detail)
    for hop in range(2000):
        options = (
            [full]
            if hop % 2
            else [
                (generator.uniform(0, duration * 0.4), generator.uniform(duration * 0.6, duration))
                for _ in range(24)
            ]
        )

        def inward_score(target):
            mapped = map_affine_range(*floor, TimingInterval(*current), TimingInterval(*target))
            truth = tuple(exact_map(value, current, target) for value in floor)
            return Fraction(mapped[0]) - truth[0] + truth[1] - Fraction(mapped[1])

        target = max(options, key=inward_score)
        floor, current = replay_server_save(floor, current, target, duration), target
        if hop < HTTP_HOPS:
            detail = save_mapping(app, route, detail, target)
            assert (endpoints(detail), synchronization(detail)) == (floor, current), hop
        restored = tuple(exact_map(value, target, full) for value in floor)
        assert restored[0] <= original[0], hop
        assert restored[1] >= original[1], hop


def test_outward_floor_preserves_a_missing_end():
    source = TimingInterval(0, 60)
    target = TimingInterval(55.33265554574976, 55.332655545751756)
    start, end = _map_policy_floor(0.001, None, source, target)
    exact = exact_map(0.001, (0, 60), (target.start_seconds, target.end_seconds))
    assert Fraction(start) <= exact < Fraction(math.nextafter(start, math.inf))
    assert end is None


def test_unrepresentable_covering_floor_has_a_safe_conflict():
    with pytest.raises(ReviewPolicyConflict) as caught:
        _map_policy_floor(
            0.0,
            0.5,
            TimingInterval(math.nextafter(0, math.inf), 1),
            TimingInterval(0, 0.5),
            duration_seconds=1,
        )
    assert caught.value.code == "review_policy_timing_precision"
