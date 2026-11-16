"""MU-15: avatar uploads are bounded, decoded, re-encoded, stored privately, and served safely."""

import hashlib
import io

import pytest
from PIL import Image
from sqlalchemy import select

from scanner.persistence.rows.db_auth import UserAvatarRow
from scanner.security.account_avatars import (
    AVATAR_SIDE,
    MAX_UPLOAD_BYTES,
    AvatarRejected,
    decode_avatar,
)
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, png
from tests.users_api_http_support import users_app as users_app


def _encoded(image: Image.Image, fmt: str, **options) -> bytes:
    output = io.BytesIO()
    image.save(output, format=fmt, **options)
    return output.getvalue()


def test_uploads_become_small_png_without_metadata():
    photo = Image.new("RGB", (1200, 600), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010E] = "private description"
    avatar = decode_avatar(_encoded(photo, "JPEG", exif=exif.tobytes()))
    stored = Image.open(io.BytesIO(avatar.image))
    assert stored.format == "PNG" and max(stored.size) == AVATAR_SIDE
    assert stored.size == (AVATAR_SIDE, AVATAR_SIDE // 2)
    assert b"private description" not in avatar.image
    assert avatar.digest == hashlib.sha256(avatar.image).hexdigest()
    animated = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
    gif = io.BytesIO()
    animated[0].save(gif, format="GIF", save_all=True, append_images=animated[1:])
    assert Image.open(io.BytesIO(decode_avatar(gif.getvalue()).image)).n_frames == 1


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
@pytest.mark.parametrize("side", [32, 600])
def test_uploads_drop_embedded_color_profiles(fmt, side):
    profile = b"private-color-profile" * 16
    upload = _encoded(Image.new("RGB", (side, side), (10, 120, 200)), fmt, icc_profile=profile)
    assert Image.open(io.BytesIO(upload)).info["icc_profile"] == profile
    avatar = decode_avatar(upload)
    stored = Image.open(io.BytesIO(avatar.image))
    assert "icc_profile" not in stored.info and b"private-color-profile" not in avatar.image


@pytest.mark.parametrize(
    ("upload", "expected"),
    [
        (b"", "avatar_empty"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "avatar_not_image"),
        (_encoded(Image.new("RGB", (4, 4)), "BMP"), "avatar_not_image"),
        (png()[:40], "avatar_not_image"),
        (_encoded(Image.new("L", (4097, 2)), "PNG"), "avatar_dimensions"),
        (b"\x89PNG" + b"0" * MAX_UPLOAD_BYTES, "avatar_too_large"),
    ],
)
def test_unacceptable_uploads_are_rejected(upload, expected):
    with pytest.raises(AvatarRejected) as rejected:
        decode_avatar(upload)
    assert rejected.value.code == expected


def _put(app, user, target, content, **headers):
    return app.send(
        "PUT",
        f"/api/users/{target}/avatar",
        user,
        content=content,
        headers={"Content-Type": "image/png", **headers},
    )


def test_local_accounts_upload_view_and_remove_avatars(users_app):
    response = _put(users_app, USER, USER, png())
    assert response.status_code == 200, response.text
    digest = response.json()["avatar"]
    image = users_app.send("GET", f"/api/users/{USER}/avatar", USER)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert hashlib.sha256(image.content).hexdigest() == digest
    assert image.headers["etag"] == f'"{digest}"'
    assert image.headers["x-content-type-options"] == "nosniff"
    assert users_app.send("GET", f"/api/users/{USER}/avatar", ADMIN).content == image.content
    assert code(users_app.send("GET", f"/api/users/{USER}/avatar", PEER_USER)) == (
        403,
        "capability_denied",
    )
    assert (
        users_app.scalar(select(UserAvatarRow.digest).where(UserAvatarRow.user_id == USER))
        == digest
    )
    assert _put(users_app, ADMIN, USER, png(color=(0, 0, 0, 255))).json()["avatar"] != digest
    assert code(_put(users_app, ADMIN, PEER_ADMIN, png())) == (403, "protected_account")
    assert code(_put(users_app, USER, PEER_USER, png())) == (404, "not_found")
    removed = users_app.send("DELETE", f"/api/users/{USER}/avatar", USER)
    assert removed.status_code == 200 and removed.json()["avatar"] is None
    assert code(users_app.send("GET", f"/api/users/{USER}/avatar", USER)) == (
        404,
        "avatar_not_found",
    )
    assert users_app.send("GET", f"/api/users/{OWNER}/avatar", ADMIN).status_code == 404


def test_avatar_uploads_are_bounded_and_decoded(users_app):
    oversized = b"\x89PNG" + b"0" * MAX_UPLOAD_BYTES
    assert code(_put(users_app, USER, USER, oversized)) == (413, "avatar_too_large")
    assert code(_put(users_app, USER, USER, b"GIF89a-not-really")) == (422, "avatar_not_image")
    assert users_app.scalar(select(UserAvatarRow.user_id)) is None


def test_linked_accounts_cannot_replace_their_jellyfin_avatar(users_app):
    users_app.link(PEER_USER)
    assert code(_put(users_app, PEER_USER, PEER_USER, png())) == (409, "linked_profile_field")
    assert code(users_app.send("DELETE", f"/api/users/{PEER_USER}/avatar", ADMIN)) == (
        409,
        "linked_profile_field",
    )
