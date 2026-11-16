"""Pure, fail-closed client-address resolution for local authentication."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork: TypeAlias = ipaddress.IPv4Network | ipaddress.IPv6Network
ClientAddressReason: TypeAlias = Literal[
    "direct",
    "forwarded",
    "untrusted_forwarding_header",
    "missing_forwarding_header",
    "conflicting_forwarding_headers",
    "malformed_forwarding_header",
    "forwarding_chain_too_long",
    "all_hops_trusted",
    "missing_direct_peer",
]

_MAX_FORWARDED_HEADER_BYTES = 8_192
_MAX_FORWARDED_CHAIN_ENTRIES = 32
_OWS = " \t"
_TOKEN_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


@dataclass(frozen=True, slots=True)
class ClientAddressResolution:
    """A canonical client address and a fail-closed locality decision.

    ``throttle_key`` is always suitable for a rate-limit bucket: it is the retained
    canonical direct peer on forwarding failures and ``"unknown"`` without one.
    ``forwarding_valid`` means forwarding safely resolved a client address, rather
    than merely that a forwarding header was syntactically well-formed.
    """

    address: str | None
    is_local: bool
    forwarding_valid: bool
    forwarding_used: bool
    reason_code: ClientAddressReason
    throttle_key: str


def resolve_client_address(
    direct_peer: str | tuple[str, int] | None,
    raw_headers: Sequence[tuple[bytes, bytes]],
    trusted_proxy_networks: Iterable[IPNetwork],
) -> ClientAddressResolution:
    """Resolve an ASGI peer and raw ordered headers without any DNS lookup.

    The caller must pass the ASGI scope's ``client`` tuple (or just its host) and the
    uncollapsed ASGI ``headers`` sequence. Header parsing and every failure path are
    intentionally conservative because this result keys login throttling.
    """

    direct_address = _parse_direct_peer(direct_peer)
    if direct_address is None:
        return _failure(None, "missing_direct_peer")

    trusted_networks = tuple(trusted_proxy_networks)
    forwarded_values, x_forwarded_for_values = _forwarding_header_values(raw_headers)
    has_forwarding = bool(forwarded_values or x_forwarded_for_values)
    if not _is_trusted_proxy(direct_address, trusted_networks):
        if has_forwarding:
            return _failure(direct_address, "untrusted_forwarding_header")
        return _direct(direct_address)

    if forwarded_values and x_forwarded_for_values:
        return _failure(direct_address, "conflicting_forwarding_headers")
    if not forwarded_values and not x_forwarded_for_values:
        return _failure(direct_address, "missing_forwarding_header")

    values = forwarded_values or x_forwarded_for_values
    if sum(len(value) for value in values) > _MAX_FORWARDED_HEADER_BYTES:
        return _failure(direct_address, "malformed_forwarding_header")

    try:
        decoded_values = [value.decode("ascii") for value in values]
        if forwarded_values:
            chain = _parse_forwarded(",".join(decoded_values))
        else:
            chain = _parse_x_forwarded_for(",".join(decoded_values))
    except _ForwardingHeaderTooLong:
        return _failure(direct_address, "forwarding_chain_too_long")
    except (_MalformedForwardingHeader, UnicodeDecodeError):
        return _failure(direct_address, "malformed_forwarding_header")

    for candidate in reversed(chain):
        if not _is_trusted_proxy(candidate, trusted_networks):
            return ClientAddressResolution(
                address=str(candidate),
                is_local=_is_local(candidate),
                forwarding_valid=True,
                forwarding_used=True,
                reason_code="forwarded",
                throttle_key=str(candidate),
            )
    return _failure(direct_address, "all_hops_trusted")


class _MalformedForwardingHeader(ValueError):
    """Raised for a forwarding family that does not meet the approved grammar."""


class _ForwardingHeaderTooLong(_MalformedForwardingHeader):
    """Raised when a selected forwarding family contains too many entries."""


def _direct(address: IPAddress) -> ClientAddressResolution:
    return ClientAddressResolution(
        address=str(address),
        is_local=_is_local(address),
        forwarding_valid=False,
        forwarding_used=False,
        reason_code="direct",
        throttle_key=str(address),
    )


def _failure(
    address: IPAddress | None, reason_code: ClientAddressReason
) -> ClientAddressResolution:
    canonical_address = str(address) if address is not None else None
    return ClientAddressResolution(
        address=canonical_address,
        is_local=False,
        forwarding_valid=False,
        forwarding_used=False,
        reason_code=reason_code,
        throttle_key=canonical_address or "unknown",
    )


def _parse_direct_peer(direct_peer: str | tuple[str, int] | None) -> IPAddress | None:
    if isinstance(direct_peer, tuple):
        if len(direct_peer) != 2 or not isinstance(direct_peer[0], str):
            return None
        value = direct_peer[0]
    elif isinstance(direct_peer, str):
        value = direct_peer
    else:
        return None
    return _parse_address(value)


def _forwarding_header_values(
    raw_headers: Sequence[tuple[bytes, bytes]],
) -> tuple[list[bytes], list[bytes]]:
    forwarded_values: list[bytes] = []
    x_forwarded_for_values: list[bytes] = []
    for name, value in raw_headers:
        if name.lower() == b"forwarded":
            forwarded_values.append(value)
        elif name.lower() == b"x-forwarded-for":
            x_forwarded_for_values.append(value)
    return forwarded_values, x_forwarded_for_values


def _parse_forwarded(raw_value: str) -> list[IPAddress]:
    elements = _split_delimited(raw_value, ",")
    _enforce_chain_length(elements)
    return [_parse_forwarded_element(element) for element in elements]


def _parse_forwarded_element(raw_element: str) -> IPAddress:
    """Parse one Forwarded header element into validated proxy metadata."""
    element = _strip_ows(raw_element)
    if not element:
        raise _MalformedForwardingHeader

    parameters = _split_delimited(element, ";")
    seen_names: set[str] = set()
    for_address: IPAddress | None = None
    for_count = 0
    for parameter in parameters:
        name, value = _parse_forwarded_parameter(parameter)
        if name in seen_names:
            raise _MalformedForwardingHeader
        seen_names.add(name)
        if name == "for":
            for_count += 1
            for_address = _parse_forwarded_for_value(value)
    if for_count != 1 or for_address is None:
        raise _MalformedForwardingHeader
    return for_address


def _parse_forwarded_parameter(raw_parameter: str) -> tuple[str, str]:
    parameter = _strip_ows(raw_parameter)
    if not parameter or "=" not in parameter:
        raise _MalformedForwardingHeader
    raw_name, value = parameter.split("=", 1)
    if not raw_name or not value or raw_name != _strip_ows(raw_name) or value != _strip_ows(value):
        raise _MalformedForwardingHeader
    if not _is_token(raw_name):
        raise _MalformedForwardingHeader
    _validate_forwarded_parameter_value(value)
    return raw_name.lower(), value


def _validate_forwarded_parameter_value(value: str) -> None:
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise _MalformedForwardingHeader
        for character in value[1:-1]:
            if character in {'"', "\\"} or ord(character) < 32 or ord(character) == 127:
                raise _MalformedForwardingHeader
        return
    if not _is_token(value):
        raise _MalformedForwardingHeader


def _parse_forwarded_for_value(value: str) -> IPAddress:
    if value.startswith('"'):
        return _parse_quoted_forwarded_node(value[1:-1])
    return _parse_ipv4_without_port(value)


def _parse_quoted_forwarded_node(value: str) -> IPAddress:
    if value.startswith("["):
        return _parse_bracketed_ipv6(value)
    return _parse_ipv4_with_optional_port(value)


def _parse_x_forwarded_for(raw_value: str) -> list[IPAddress]:
    elements = _split_delimited(raw_value, ",")
    _enforce_chain_length(elements)
    return [_parse_x_forwarded_for_node(_strip_ows(element)) for element in elements]


def _parse_x_forwarded_for_node(value: str) -> IPAddress:
    if not value or any(character in _OWS for character in value):
        raise _MalformedForwardingHeader
    if value.startswith("["):
        return _parse_bracketed_ipv6(value)

    address = _parse_address(value)
    if address is not None:
        return address
    return _parse_ipv4_with_optional_port(value)


def _parse_bracketed_ipv6(value: str) -> IPAddress:
    """Parse a bracketed IPv6 authority and its optional port."""
    closing_bracket = value.find("]")
    if closing_bracket <= 1:
        raise _MalformedForwardingHeader
    address = _parse_address(value[1:closing_bracket])
    if not isinstance(address, ipaddress.IPv6Address):
        raise _MalformedForwardingHeader
    suffix = value[closing_bracket + 1 :]
    if suffix:
        if not suffix.startswith(":"):
            raise _MalformedForwardingHeader
        _validate_port(suffix[1:])
    return address


def _parse_ipv4_without_port(value: str) -> IPAddress:
    address = _parse_address(value)
    if not isinstance(address, ipaddress.IPv4Address):
        raise _MalformedForwardingHeader
    return address


def _parse_ipv4_with_optional_port(value: str) -> IPAddress:
    if ":" not in value:
        return _parse_ipv4_without_port(value)
    host, separator, port = value.partition(":")
    if not separator or ":" in port:
        raise _MalformedForwardingHeader
    _validate_port(port)
    return _parse_ipv4_without_port(host)


def _validate_port(value: str) -> None:
    if not value or any(character not in "0123456789" for character in value):
        raise _MalformedForwardingHeader
    if not 1 <= int(value) <= 65_535:
        raise _MalformedForwardingHeader


def _split_delimited(value: str, delimiter: str) -> list[str]:
    """Split a header on delimiters outside quoted strings and escaped text."""
    values: list[str] = []
    start = 0
    in_quote = False
    for index, character in enumerate(value):
        if character == "\\":
            raise _MalformedForwardingHeader
        if character == '"':
            in_quote = not in_quote
        elif character == delimiter and not in_quote:
            values.append(value[start:index])
            start = index + 1
        elif ord(character) < 32 and character != "\t" or ord(character) == 127:
            raise _MalformedForwardingHeader
    if in_quote:
        raise _MalformedForwardingHeader
    values.append(value[start:])
    if any(not _strip_ows(item) for item in values):
        raise _MalformedForwardingHeader
    return values


def _enforce_chain_length(elements: Sequence[str]) -> None:
    if len(elements) > _MAX_FORWARDED_CHAIN_ENTRIES:
        raise _ForwardingHeaderTooLong


def _parse_address(value: str) -> IPAddress | None:
    if not value or "%" in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted_proxy(address: IPAddress, networks: Sequence[IPNetwork]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def _is_local(address: IPAddress) -> bool:
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or (
            isinstance(address, ipaddress.IPv6Address)
            and address in ipaddress.IPv6Network("fc00::/7")
        )
    )


def _is_token(value: str) -> bool:
    return bool(value) and all(character in _TOKEN_CHARACTERS for character in value)


def _strip_ows(value: str) -> str:
    return value.strip(_OWS)
