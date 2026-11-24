"""Pure RFC 9421 request signing for the Editarr community profile v1."""

import base64
import hashlib
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TAG = "editarr-community-request-v1"
LABEL = "editarr"
ALGORITHM = "ed25519"
READ_COMPONENTS = (
    "@method", "@scheme", "@authority", "@path", "@query", "content-digest",
)
WRITE_COMPONENTS = (
    *READ_COMPONENTS, "content-type", "idempotency-key",
)


class SignatureProfileError(ValueError):
    """A request cannot be represented unambiguously in the signing profile."""


def url_components(url: str) -> tuple[str, str, str, str]:
    """Split a canonical HTTPS URI into scheme, authority, path and `?query`, or raise."""
    parts = urlsplit(url)
    if (not url.startswith("https://") or not parts.hostname
            or parts.username or parts.password
            or "#" in url or not parts.netloc or not url.isascii()
            or re.search(r"%(?![0-9A-Fa-f]{2})|[\x00-\x20\x7f]", url)):
        raise SignatureProfileError("Expected an unambiguous HTTPS target URI")
    try:
        port = parts.port
    except ValueError as error:
        raise SignatureProfileError("Invalid target port") from error
    if port is not None and port != 443:
        authority = f"{parts.hostname}:{port}"
    else:
        authority = parts.hostname
    if not re.fullmatch(r"[a-z0-9.\-]+(?::[0-9]{1,5})?", authority):
        raise SignatureProfileError("Unsupported target authority")
    if parts.netloc != authority:
        raise SignatureProfileError("Target URI must use canonical authority spelling")
    path = parts.path or "/"
    if (not path.startswith("/") or path.startswith("//")
            or re.search(r"%2[eEfF]|(^|/)\.\.?(/|$)", path)):
        raise SignatureProfileError("Ambiguous target path")
    return "https", authority, path, "?" + parts.query


def is_canonical_service_origin(service: str) -> bool:
    """Return whether `service` is exactly `https://authority` with no path or query."""
    try:
        _, authority, path, query = url_components(service)
    except SignatureProfileError:
        return False
    return service == f"https://{authority}" and path == "/" and query == "?"


def sign_request(
    *, private_key: Ed25519PrivateKey, key_id: str, method: str, url: str,
    body: bytes, created: int, expires: int, nonce: str,
    content_type: str | None = None, content_encoding: str | None = None,
    operation_id: str | None = None,
) -> dict[str, str | bytes]:
    """Sign exact transmitted bytes; caller supplies fresh nonce and expiry per attempt."""
    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(body, bytes):
        raise SignatureProfileError("Expected Ed25519 key and exact body bytes")
    if (type(created) is not int or type(expires) is not int or created < 0
            or expires <= created or expires > 9_007_199_254_740_991):
        raise SignatureProfileError("Invalid signature time bounds")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key_id):
        raise SignatureProfileError("Invalid key identifier")
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", nonce):
        raise SignatureProfileError("Nonce must be canonical 128-bit base64url")
    try:
        nonce_bytes = base64.urlsafe_b64decode(nonce + "==")
    except ValueError as error:
        raise SignatureProfileError("Invalid nonce") from error
    if (len(nonce_bytes) != 16
            or base64.urlsafe_b64encode(nonce_bytes).decode().rstrip("=") != nonce):
        raise SignatureProfileError("Invalid nonce")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise SignatureProfileError("Unsupported HTTP method")
    mutation = method != "GET"
    if mutation != (operation_id is not None):
        raise SignatureProfileError("Mutation requires exactly one idempotency key")
    if mutation:
        if (not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", operation_id)
                or content_type not in {"application/json", "application/octet-stream"}):
            raise SignatureProfileError("Invalid mutation type or idempotency key")
    elif body or content_type is not None or content_encoding is not None:
        raise SignatureProfileError("Read request must have empty body and no content metadata")
    if content_encoding not in {None, "gzip"}:
        raise SignatureProfileError("Unsupported content encoding")
    scheme, authority, path, query = url_components(url)
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"
    components = (*WRITE_COMPONENTS, "content-encoding") if content_encoding else (
        WRITE_COMPONENTS if mutation else READ_COMPONENTS
    )
    values = {
        "@method": method, "@scheme": scheme, "@authority": authority,
        "@path": path, "@query": query, "content-digest": digest,
        "content-type": content_type, "content-encoding": content_encoding,
        "idempotency-key": operation_id,
    }
    parameters = (
        "(" + " ".join(f'"{name}"' for name in components) + ")"
        f';created={created};expires={expires};nonce="{nonce}";keyid="{key_id}"'
        f';alg="{ALGORITHM}";tag="{TAG}"'
    )
    base = ("\n".join(f'"{name}": {values[name]}' for name in components)
            + '\n"@signature-params": ' + parameters).encode("ascii")
    signature = base64.b64encode(private_key.sign(base)).decode("ascii")
    result: dict[str, str | bytes] = {
        "signature-input": f"{LABEL}={parameters}",
        "signature": f"{LABEL}=:{signature}:",
        "content-digest": digest,
        "signature-base": base,
    }
    if mutation:
        result["content-type"] = content_type
        result["idempotency-key"] = operation_id
    if content_encoding:
        result["content-encoding"] = content_encoding
    return result
