"""Principal-partitioned URLs and non-storable responses for protected artwork."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from .catalog_access import CatalogViewer


def artwork_url(url: str, viewer: CatalogViewer) -> str:
    """Partition a browser cache key without granting access to the named item."""
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["principal"] = viewer.artwork_scope
    return urlunsplit(parsed._replace(query=urlencode(query)))


def artwork_headers(viewer: CatalogViewer, content: bytes) -> dict[str, str]:
    digest = hashlib.sha256(viewer.artwork_scope.encode() + b"\0" + content).hexdigest()
    return {
        "Cache-Control": "private, no-store",
        "Vary": "Cookie, Authorization",
        "ETag": f'"{digest}"',
    }
