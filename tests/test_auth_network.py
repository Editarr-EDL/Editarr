import ipaddress

import pytest

from scanner.security.auth_network import resolve_client_address


def headers(*pairs: tuple[str, str]) -> list[tuple[bytes, bytes]]:
    return [(name.encode("ascii"), value.encode("ascii")) for name, value in pairs]


def networks(*values: str):
    return [ipaddress.ip_network(value) for value in values]


@pytest.mark.parametrize(
    ("peer", "expected_address", "expected_local"),
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("10.1.2.3", "10.1.2.3", True),
        ("169.254.1.2", "169.254.1.2", True),
        ("8.8.8.8", "8.8.8.8", False),
        ("::1", "::1", True),
        ("fc00::7", "fc00::7", True),
        ("fe80::7", "fe80::7", True),
        ("2001:4860:4860::8888", "2001:4860:4860::8888", False),
        ("::ffff:127.0.0.1", "127.0.0.1", True),
        (("::ffff:8.8.8.8", 1234), "8.8.8.8", False),
    ],
)
def test_direct_peers_are_normalized_and_classified(peer, expected_address, expected_local):
    result = resolve_client_address(peer, [], [])

    assert result.address == expected_address
    assert result.throttle_key == expected_address
    assert result.is_local is expected_local
    assert result.reason_code == "direct"
    assert result.forwarding_valid is False
    assert result.forwarding_used is False


@pytest.mark.parametrize("peer", [None, "example.test", "fe80::1%en0", ("", 1234)])
def test_missing_or_malformed_direct_peer_fails_closed(peer):
    result = resolve_client_address(
        peer,
        headers(("Forwarded", "for=127.0.0.1")),
        networks("127.0.0.1"),
    )

    assert result.address is None
    assert result.throttle_key == "unknown"
    assert result.is_local is False
    assert result.reason_code == "missing_direct_peer"


def test_untrusted_peer_ignores_spoofed_forwarding_headers():
    result = resolve_client_address(
        "192.0.2.20",
        headers(("Forwarded", 'for="[::1]:443"')),
        networks("10.0.0.0/8"),
    )

    assert result.address == "192.0.2.20"
    assert result.throttle_key == "192.0.2.20"
    assert result.is_local is False
    assert result.reason_code == "untrusted_forwarding_header"


@pytest.mark.parametrize(
    ("raw_headers", "expected_reason"),
    [
        (
            headers(("Forwarded", "for=192.0.2.1"), ("X-Forwarded-For", "127.0.0.1")),
            "conflicting_forwarding_headers",
        ),
        ([], "missing_forwarding_header"),
    ],
)
def test_trusted_peer_requires_exactly_one_forwarding_header_family(raw_headers, expected_reason):
    result = resolve_client_address("127.0.0.1", raw_headers, networks("127.0.0.1"))

    assert result.address == "127.0.0.1"
    assert result.is_local is False
    assert result.reason_code == expected_reason


@pytest.mark.parametrize(
    ("raw_headers", "expected_address"),
    [
        (headers(("Forwarded", "for=8.8.8.8")), "8.8.8.8"),
        (headers(("fOrWaRdEd", ' \tfor="192.0.2.4:443" \t')), "192.0.2.4"),
        (headers(("Forwarded", 'for="[2001:db8::4]:443"')), "2001:db8::4"),
        (headers(("X-Forwarded-For", "8.8.8.8:443")), "8.8.8.8"),
        (headers(("X-Forwarded-For", "2001:db8::4")), "2001:db8::4"),
        (headers(("X-Forwarded-For", "[2001:db8::4]:443")), "2001:db8::4"),
        (headers(("X-Forwarded-For", "::ffff:192.0.2.4")), "192.0.2.4"),
    ],
)
def test_trusted_peer_accepts_documented_forwarding_node_syntax(raw_headers, expected_address):
    result = resolve_client_address("127.0.0.1", raw_headers, networks("127.0.0.1"))

    assert result.address == expected_address
    assert result.throttle_key == expected_address
    assert result.reason_code == "forwarded"
    assert result.forwarding_valid is True
    assert result.forwarding_used is True


def test_repeated_forwarding_fields_preserve_wire_order_for_chain_selection():
    result = resolve_client_address(
        "10.0.0.9",
        headers(("Forwarded", "for=127.0.0.1"), ("Forwarded", "for=8.8.4.4")),
        networks("10.0.0.0/8"),
    )

    assert result.address == "8.8.4.4"
    assert result.reason_code == "forwarded"


def test_repeated_x_forwarded_for_fields_preserve_wire_order_for_chain_selection():
    result = resolve_client_address(
        "10.0.0.9",
        headers(("X-Forwarded-For", "127.0.0.1"), ("X-Forwarded-For", "8.8.4.4")),
        networks("10.0.0.0/8"),
    )

    assert result.address == "8.8.4.4"
    assert result.reason_code == "forwarded"


def test_forwarded_validates_other_parameters_before_ignoring_them():
    accepted = resolve_client_address(
        "127.0.0.1",
        headers(("Forwarded", ' for=8.8.8.8 \t;\tproto="https" ')),
        networks("127.0.0.1"),
    )
    duplicate = resolve_client_address(
        "127.0.0.1",
        headers(("Forwarded", "for=8.8.8.8;proto=http;PROTO=https")),
        networks("127.0.0.1"),
    )
    invalid_value = resolve_client_address(
        "127.0.0.1",
        headers(("Forwarded", "for=8.8.8.8;proto=not valid")),
        networks("127.0.0.1"),
    )

    assert accepted.reason_code == "forwarded"
    assert duplicate.reason_code == "malformed_forwarding_header"
    assert invalid_value.reason_code == "malformed_forwarding_header"


def test_trusted_chain_selects_first_untrusted_hop_from_the_right():
    result = resolve_client_address(
        "10.0.0.9",
        headers(("X-Forwarded-For", "127.0.0.1, 8.8.8.8, 10.0.0.8")),
        networks("10.0.0.0/8"),
    )

    assert result.address == "8.8.8.8"
    assert result.is_local is False
    assert result.reason_code == "forwarded"


def test_attacker_added_left_entries_do_not_change_selected_client():
    result = resolve_client_address(
        "10.0.0.9",
        headers(("X-Forwarded-For", "127.0.0.1, 10.2.3.4, 8.8.8.8, 10.0.0.8")),
        networks("10.0.0.0/8"),
    )

    assert result.address == "8.8.8.8"
    assert result.reason_code == "forwarded"


def test_all_trusted_chain_fails_closed_without_selecting_the_leftmost_address():
    result = resolve_client_address(
        "10.0.0.9",
        headers(("X-Forwarded-For", "127.0.0.1, 10.0.0.8")),
        networks("127.0.0.0/8", "10.0.0.0/8"),
    )

    assert result.address == "10.0.0.9"
    assert result.is_local is False
    assert result.reason_code == "all_hops_trusted"


@pytest.mark.parametrize(
    "raw_headers",
    [
        headers(("Forwarded", "for=unknown")),
        headers(("Forwarded", "for=_hidden")),
        headers(("Forwarded", "for=example.test")),
        headers(("Forwarded", "for=2001:db8::1")),
        headers(("Forwarded", 'for="[2001:db8::1"')),
        headers(("Forwarded", 'for="[2001:db8::1]";for=192.0.2.1')),
        headers(("Forwarded", 'for="192.0.2.1\\\""')),
        headers(("Forwarded", "for =192.0.2.1")),
        headers(("Forwarded", "for= 192.0.2.1")),
        headers(("X-Forwarded-For", "")),
        headers(("X-Forwarded-For", "192.0.2.1, , 198.51.100.2")),
        headers(("X-Forwarded-For", '"192.0.2.1"')),
        headers(("X-Forwarded-For", "[192.0.2.1]:443")),
        headers(("X-Forwarded-For", "[2001:db8::1]x")),
        headers(("X-Forwarded-For", "8.8.8.8 \t 10.0.0.1")),
        headers(("X-Forwarded-For", "192.0.2.1:0")),
        headers(("X-Forwarded-For", "192.0.2.1:65536")),
        headers(("X-Forwarded-For", "fe80::1%en0")),
        headers(("X-Forwarded-For", "192.0.2.1\n127.0.0.1")),
        [(b"X-Forwarded-For", b"\xff")],
    ],
)
def test_malformed_forwarding_syntax_fails_closed(raw_headers):
    result = resolve_client_address("127.0.0.1", raw_headers, networks("127.0.0.1"))

    assert result.address == "127.0.0.1"
    assert result.throttle_key == "127.0.0.1"
    assert result.is_local is False
    assert result.reason_code == "malformed_forwarding_header"


def test_forwarding_header_byte_limit_and_chain_limit_fail_closed():
    oversized = "x" * 8_193
    overlong_chain = ",".join("192.0.2.1" for _ in range(33))

    oversized_result = resolve_client_address(
        "127.0.0.1", headers(("X-Forwarded-For", oversized)), networks("127.0.0.1")
    )
    overlong_result = resolve_client_address(
        "127.0.0.1", headers(("X-Forwarded-For", overlong_chain)), networks("127.0.0.1")
    )

    assert oversized_result.reason_code == "malformed_forwarding_header"
    assert oversized_result.is_local is False
    assert overlong_result.reason_code == "forwarding_chain_too_long"
    assert overlong_result.is_local is False
