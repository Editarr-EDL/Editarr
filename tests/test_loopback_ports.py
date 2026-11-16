"""Installed verification issues loopback ports in pairs inside the configured span."""

import socket

import pytest

from scripts import loopback_ports


@pytest.fixture
def ports(monkeypatch):
    monkeypatch.setattr(loopback_ports, "_ISSUED", set())
    return monkeypatch


def _free_block(size):
    """A run of currently free loopback ports, found from an ephemeral base."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        if base + size <= 65535 and all(
            loopback_ports._free(port) for port in range(base, base + size)
        ):
            return base
    pytest.skip("no free run of loopback ports")


def test_each_issued_port_and_its_restart_successor_stay_inside_the_span(ports):
    base = _free_block(4)
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base}-{base + 3}")
    assert [loopback_ports.available_port() for _ in range(2)] == [base, base + 2]
    with pytest.raises(RuntimeError, match="no free loopback port pair"):
        loopback_ports.available_port()


def test_a_span_without_room_for_the_successor_issues_nothing(ports):
    base = _free_block(4)
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base}-{base + 2}")
    assert loopback_ports.available_port() == base
    with pytest.raises(RuntimeError, match="no free loopback port pair"):
        loopback_ports.available_port()
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base + 3}-{base + 3}")
    with pytest.raises(ValueError, match="at least two ports"):
        loopback_ports.available_port()


def test_an_occupied_successor_skips_the_candidate(ports):
    base = _free_block(4)
    ports.setenv("EDITARR_VERIFY_PORTS", f"{base}-{base + 3}")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", base + 1))
        assert loopback_ports.available_port() == base + 2
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", base))
        with pytest.raises(RuntimeError, match="no free loopback port pair"):
            loopback_ports.available_port()


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
