"""Decode an uploaded avatar and re-encode it as a small PNG.

Only the decoded pixels survive: metadata, extra frames, and any bytes a
polyglot file hides are dropped. Inputs are bounded before decoding by byte
length, by declared dimensions, and by Pillow's decompression-bomb limit.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SOURCE_SIDE = 4096
AVATAR_SIDE = 256
FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF"})


class AvatarRejected(ValueError):
    """An upload that is not an acceptable image. `code` is the client-facing reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Avatar:
    digest: str
    """SHA-256 of `image`, which clients may use as a cache key."""
    image: bytes


def decode_avatar(upload: bytes) -> Avatar:
    """Return the stored form of an uploaded image, or raise `AvatarRejected`."""

    if not upload:
        raise AvatarRejected("avatar_empty", "Choose an image file")
    if len(upload) > MAX_UPLOAD_BYTES:
        raise AvatarRejected("avatar_too_large", "Avatar images must be 5 MiB or smaller")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(upload), formats=sorted(FORMATS)) as source:
                width, height = source.size
                if max(width, height) > MAX_SOURCE_SIDE or min(width, height) < 1:
                    raise AvatarRejected(
                        "avatar_dimensions",
                        f"Avatar images must be at most {MAX_SOURCE_SIDE} pixels per side",
                    )
                source.seek(0)
                frame = source.convert("RGBA")
    except AvatarRejected:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
        Warning,
    ) as error:
        raise AvatarRejected("avatar_not_image", "The file is not a supported image") from error
    frame.thumbnail((AVATAR_SIDE, AVATAR_SIDE))
    # `convert` copies metadata such as an ICC profile, which PNG encoding would keep.
    frame.info.clear()
    output = io.BytesIO()
    frame.save(output, format="PNG", optimize=True)
    image = output.getvalue()
    return Avatar(hashlib.sha256(image).hexdigest(), image)
