"""Length-prefixed message framing for community master snapshot receipts and signatures."""


def framed(domain: str, fields: tuple[str, ...], issued_at: int | None = None) -> bytes:
    """Return `framed(domain, fields[, issuedAt])` from the community contract conventions.

    The ASCII domain, a zero byte, each UTF-8 field prefixed by its u32 big-endian byte
    length, then `issued_at` as a u64 big-endian when supplied.
    """
    encoded = tuple(field.encode("utf-8") for field in fields)
    body = (
        domain.encode("ascii")
        + b"\0"
        + b"".join(len(field).to_bytes(4, "big") + field for field in encoded)
    )
    return body if issued_at is None else body + issued_at.to_bytes(8, "big")
