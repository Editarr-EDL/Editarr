# ruff: noqa: F811
"""JF-04 Quick Connect limits: starting codes is throttled per client, per IPv6 /64,
and across all clients, and floods of availability and status polls reuse one
recent Jellyfin answer instead of reaching Jellyfin each time.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_network

import pytest

import scanner.http.auth_api as auth_api
import scanner.http.jellyfin_quick_connect_api as quick_connect_api
from scanner.db import JellyfinSettingsRow
from scanner.security.auth_network import resolve_client_address
from scanner.security.jellyfin_accounts.quick_connect import QuickConnectAttempts
from tests.jellyfin_quick_connect_support import (
    HANDLE,
    QC,
    _complete,
    _start,
    _status,
    _use_handle,
    app,
    boundary,
)
from tests.multi_user_access_support import ORIGIN

__all__ = ["app", "boundary"]


def test_starting_codes_is_limited_per_client(app):
    for _ in range(10):
        _start(app)

    throttled = app.client.post(f"{QC}/initiate", headers=ORIGIN)

    assert (throttled.status_code, throttled.json()["code"]) == (429, "quick_connect_throttled")
    assert int(throttled.headers["Retry-After"]) > 0


def test_quick_connect_checks_the_request_origin(app):
    refused = app.client.post(f"{QC}/initiate", headers={"Origin": "https://evil.example"})
    assert (refused.status_code, refused.json()["code"]) == (403, "origin_failed")


def test_without_a_jellyfin_server_quick_connect_is_not_offered(app):
    with app.accounts.factory() as db:
        db.query(JellyfinSettingsRow).delete()
        db.commit()

    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": False}
    refused = app.client.post(f"{QC}/initiate", headers=ORIGIN)
    assert refused.json()["code"] == "jellyfin_not_configured"


def _forwarded(monkeypatch):
    """Resolve each request's client from X-Forwarded-For, as behind a trusted proxy."""
    proxy, trusted = ("127.0.0.1", 1), [ip_network("127.0.0.1/32")]
    monkeypatch.setattr(
        auth_api,
        "_client_address",
        lambda request: resolve_client_address(proxy, request.scope["headers"], trusted),
    )


def _start_from(app, address):
    app.client.cookies.clear()
    return app.client.post(f"{QC}/initiate", headers={**ORIGIN, "X-Forwarded-For": address})


def _jellyfin_calls(boundary, path):
    return sum(f" {path} " in line for line in boundary.requests)


def test_a_flood_from_many_addresses_cannot_evict_a_pending_code(app, boundary, monkeypatch):
    _forwarded(monkeypatch)
    small = QuickConnectAttempts(clock=app.ticks, capacity=4)
    monkeypatch.setattr(quick_connect_api, "quick_connect_attempts", small)
    victim = _start_from(app, "192.0.2.1")
    handle = app.client.cookies[HANDLE]

    flood = [_start_from(app, f"198.51.100.{index}") for index in range(8)]

    assert [response.status_code for response in flood] == [201] * 3 + [503] * 5
    assert flood[-1].json()["code"] == "quick_connect_busy"
    _use_handle(app, handle)
    boundary.approve(victim.json()["code"], "Blake")
    assert _status(app).json()["state"] == "approved"
    assert _complete(app).status_code == 200


def test_addresses_in_one_ipv6_64_share_the_start_limit(app, monkeypatch):
    _forwarded(monkeypatch)
    for index in range(10):
        assert _start_from(app, f"2001:db8:1:2::{index + 1:x}").status_code == 201

    throttled = _start_from(app, "2001:db8:1:2:ffff::1")

    assert (throttled.status_code, throttled.json()["code"]) == (429, "quick_connect_throttled")
    assert _start_from(app, "2001:db8:1:3::1").status_code == 201


def test_starting_codes_is_limited_across_all_clients(app, monkeypatch):
    _forwarded(monkeypatch)
    started = [_start_from(app, f"198.51.100.{index}").status_code for index in range(31)]

    assert started == [201] * 30 + [429]


def test_an_availability_flood_asks_jellyfin_once(app, boundary):
    for _ in range(20):
        assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": True}

    assert _jellyfin_calls(boundary, "/QuickConnect/Enabled") == 1
    app.ticks.now += 10
    boundary.quick_connect_enabled = False
    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": False}
    assert _jellyfin_calls(boundary, "/QuickConnect/Enabled") == 2


def _slow(boundary, monkeypatch, path, callers):
    """Hold Jellyfin's answer on `path` until `callers` requests reach it, or a second passes."""
    arrived = threading.Condition()
    dispatch = boundary._dispatch

    def held(request, method):
        if request.path.startswith(path):
            with arrived:
                held.count += 1
                arrived.notify_all()
                arrived.wait_for(lambda: held.count >= callers, timeout=1)
        return dispatch(request, method)

    held.count = 0
    monkeypatch.setattr(boundary, "_dispatch", held)


def _concurrently(callers, call):
    with ThreadPoolExecutor(callers) as pool:
        return list(pool.map(lambda _: call(), range(callers)))


@pytest.mark.parametrize(("fault", "enabled"), [(None, True), ("starting", False)])
def test_concurrent_first_availability_requests_share_one_jellyfin_answer(
    app, boundary, monkeypatch, fault, enabled
):
    boundary.fault = fault
    _slow(boundary, monkeypatch, "/QuickConnect/Enabled", 20)

    answers = _concurrently(20, lambda: app.client.get(QC, headers=ORIGIN).json())

    assert answers == [{"enabled": enabled}] * 20
    assert _jellyfin_calls(boundary, "/QuickConnect/Enabled") == 1


def test_an_outage_is_reused_briefly_then_asked_again(app, boundary):
    boundary.fault = "starting"
    for _ in range(5):
        assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": False}
    assert _jellyfin_calls(boundary, "/QuickConnect/Enabled") == 1

    boundary.fault = None
    app.ticks.now += 3
    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": True}
    assert _jellyfin_calls(boundary, "/QuickConnect/Enabled") == 2


def test_concurrent_first_status_polls_ask_jellyfin_once(app, boundary, monkeypatch):
    code = _start(app)
    app.ticks.now += 1
    _slow(boundary, monkeypatch, "/QuickConnect/Connect", 20)

    answers = _concurrently(20, lambda: app.client.get(f"{QC}/status", headers=ORIGIN).json())

    assert answers == [{"state": "pending", "code": code}] * 20
    assert _jellyfin_calls(boundary, "/QuickConnect/Connect") == 1


def test_a_status_flood_asks_jellyfin_at_most_once(app, boundary):
    code = _start(app)
    for _ in range(20):
        assert app.client.get(f"{QC}/status", headers=ORIGIN).json() == {
            "state": "pending",
            "code": code,
        }

    assert _jellyfin_calls(boundary, "/QuickConnect/Connect") <= 1
    boundary.approve(code, "Blake")
    assert _status(app).json()["state"] == "approved"
    assert _jellyfin_calls(boundary, "/QuickConnect/Connect") <= 2
