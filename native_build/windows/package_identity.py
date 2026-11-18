"""Define the signing and labeling contract for Windows installer modes."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

DEVELOPMENT_PUBLISHER = "Unsigned development build - no publisher identity"
ALPHA_PUBLISHER = "Unsigned alpha build - no publisher identity"
_PUBLISHER = re.compile(r"^[\w .,&'()\-]{1,128}$", re.UNICODE)


class PackageError(RuntimeError):
    """A Windows package input or tool result is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    publisher: str
    signed: bool
    development_only: bool
    artifact_base: str
    display_name: str
    description: str


def resolve_identity(args: argparse.Namespace, version: str) -> PackageIdentity:
    if args.unsigned_alpha:
        if args.publisher or args.certificate or args.timestamp_url:
            raise PackageError(
                "--unsigned-alpha cannot be combined with production identity/signing options"
            )
        return PackageIdentity(
            publisher=ALPHA_PUBLISHER,
            signed=False,
            development_only=False,
            artifact_base=f"Editarr-v{version}-Windows-x64-Setup",
            display_name="Editarr (Unsigned Alpha)",
            description="Editarr per-user installer - unsigned alpha release",
        )

    if args.unsigned_development:
        if args.publisher or args.certificate or args.timestamp_url:
            raise PackageError(
                "--unsigned-development cannot be combined with production identity/signing options"
            )
        return PackageIdentity(
            publisher=DEVELOPMENT_PUBLISHER,
            signed=False,
            development_only=True,
            artifact_base=f"Editarr-Setup-{version}-unsigned-development",
            display_name="Editarr (Unsigned Development Build)",
            description="Editarr per-user installer - unsigned development build",
        )

    if _PUBLISHER.fullmatch(args.publisher or "") is None:
        raise PackageError("an approved Windows Publisher display name is required")
    if not args.certificate or not args.timestamp_url:
        raise PackageError(
            "production packaging requires --certificate and an HTTPS --timestamp-url"
        )
    if urlsplit(args.timestamp_url).scheme.lower() != "https":
        raise PackageError("--timestamp-url must use HTTPS")
    return PackageIdentity(
        publisher=args.publisher,
        signed=True,
        development_only=False,
        artifact_base=f"Editarr-Setup-{version}",
        display_name="Editarr",
        description="Editarr per-user installer",
    )
