"""JF-06: local sign-in, registration, password change, and password reset routes are gone."""

import pytest

from tests.multi_user_access_support import OWNER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import users_app as users_app

PASSWORD_BODY = {"username": "member4", "password": "member password two", "remember_me": False}


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/register"])
def test_signed_out_local_sign_in_and_registration_answer_not_found(users_app, path):
    response = users_app.send("POST", path, None, PASSWORD_BODY)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("POST", "/api/auth/login", 404),
        ("POST", "/api/auth/register", 404),
        ("PUT", "/api/auth/account", 404),
        ("GET", "/api/settings/registration", 404),
        ("PUT", "/api/settings/registration", 404),
        ("POST", "/api/users/1/password-reset", 404),
        # `GET /api/users` lists the directory, so FastAPI answers the old create method 405.
        ("POST", "/api/users", 405),
    ],
)
def test_the_owner_reaches_no_local_password_route(users_app, method, path, status):
    body = None if method == "GET" else PASSWORD_BODY
    assert users_app.send(method, path, OWNER, body).status_code == status
