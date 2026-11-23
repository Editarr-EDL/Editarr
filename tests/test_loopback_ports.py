"""Installed verification issues loopback ports in pairs inside the configured span."""

import socket

import pytest

from scripts import loopback_ports


@pytest.fixture
def ports(monkeypatch):
    monkeypatch.setattr(loopback_ports, "_ISSUED", set())
    return monkeypatch


@pytest.fixture
def configured_span(ports):
    """Hold port availability constant while testing configured-span selection."""
    base = 9240
    available = set(range(base, base + 4))
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base}-{base + 3}")
    ports.setattr(loopback_ports, "_free", available.__contains__)
    return base, available


def test_each_issued_port_and_its_restart_successor_stay_inside_the_span(configured_span):
    base, _ = configured_span
    assert [loopback_ports.available_port() for _ in range(2)] == [base, base + 2]
    with pytest.raises(RuntimeError, match="no free loopback port pair"):
        loopback_ports.available_port()


def test_a_span_without_room_for_the_successor_issues_nothing(ports, configured_span):
    base, _ = configured_span
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base}-{base + 2}")
    assert loopback_ports.available_port() == base
    with pytest.raises(RuntimeError, match="no free loopback port pair"):
        loopback_ports.available_port()
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base + 3}-{base + 3}")
    with pytest.raises(ValueError, match="at least two ports"):
        loopback_ports.available_port()


def test_an_occupied_successor_skips_the_candidate(configured_span):
    base, available = configured_span
    available.remove(base + 1)
    assert loopback_ports.available_port() == base + 2
    available.add(base + 1)
    available.remove(base)
    with pytest.raises(RuntimeError, match="no free loopback port pair"):
        loopback_ports.available_port()


def test_bind_probe_detects_a_real_occupied_port():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        assert not loopback_ports._free(occupied.getsockname()[1])


def test_without_a_span_ephemeral_ports_also_reserve_their_successor(ports):
    ports.delenv("EDITARR_VERIFY_PORTS", raising=False)
    first = loopback_ports.available_port()
    assert {first, first + 1} <= loopback_ports._ISSUED
    assert loopback_ports.available_port() not in {first, first + 1}


@pytest.mark.parametrize("span", ["9240", "9259-9240", "a-b", "0-1"])
def test_malformed_spans_are_refused(ports, span):
    ports.setenv("EDITARR_VERIFY_PORTS", span)
    with pytest.raises(ValueError):
        loopback_ports.available_port()
