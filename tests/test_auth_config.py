import logging

import pytest
from pydantic import ValidationError

from scanner.settings.config import Settings

AUTH_ENVIRONMENT_NAMES = (
    "EDITARR_AUTHENTICATION_METHOD",
    "EDITARR_AUTHENTICATION_REQUIRED",
    "EDITARR_AUTH_TRUSTED_PROXY_NETWORKS",
    "EDITARR_AUTH_SESSION_IDLE_SECONDS",
    "EDITARR_AUTH_SESSION_ABSOLUTE_SECONDS",
    "EDITARR_AUTH_REMEMBER_ME_IDLE_SECONDS",
    "EDITARR_AUTH_REMEMBER_ME_SECONDS",
    "EDITARR_AUTH_COOKIE_NAME",
    "EDITARR_AUTH_COOKIE_SAMESITE",
    "EDITARR_AUTH_COOKIE_SECURE",
    "EDITARR_AUTH_LOGIN_USERNAME_ATTEMPTS",
    "EDITARR_AUTH_LOGIN_ADDRESS_ATTEMPTS",
    "EDITARR_AUTH_LOGIN_WINDOW_SECONDS",
)


@pytest.fixture(autouse=True)
def clear_auth_environment(monkeypatch):
    for name in AUTH_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_authentication_settings_have_secure_defaults():
    config = Settings()

    assert config.auth_trusted_proxy_networks == []
    assert config.auth_session_idle_seconds == 30 * 60
    assert config.auth_session_absolute_seconds == 8 * 60 * 60
    assert config.auth_remember_me_idle_seconds == 7 * 24 * 60 * 60
    assert config.auth_remember_me_seconds == 30 * 24 * 60 * 60
    assert config.auth_cookie_name == "editarr_session"
    assert config.auth_cookie_samesite == "lax"
    assert config.auth_cookie_secure is True
    assert config.auth_login_username_attempts == 5
    assert config.auth_login_address_attempts == 25
    assert config.auth_login_window_seconds == 15 * 60


def test_obsolete_bypass_environment_variables_have_no_configuration_effect(monkeypatch):
    monkeypatch.setenv("EDITARR_AUTHENTICATION_METHOD", "external")
    monkeypatch.setenv("EDITARR_AUTHENTICATION_REQUIRED", "disabled_for_local_addresses")

    config = Settings()

    assert "authentication_method" not in Settings.model_fields
    assert "authentication_required" not in Settings.model_fields
    assert not hasattr(config, "resolve_authentication_configuration")


def test_environment_overrides_are_typed(monkeypatch):
    overrides = {
        "EDITARR_AUTH_TRUSTED_PROXY_NETWORKS": '["127.0.0.1", "10.0.0.0/8"]',
        "EDITARR_AUTH_SESSION_IDLE_SECONDS": "60",
        "EDITARR_AUTH_SESSION_ABSOLUTE_SECONDS": "120",
        "EDITARR_AUTH_REMEMBER_ME_IDLE_SECONDS": "3600",
        "EDITARR_AUTH_REMEMBER_ME_SECONDS": "7200",
        "EDITARR_AUTH_COOKIE_NAME": "editarr_dev",
        "EDITARR_AUTH_COOKIE_SAMESITE": "strict",
        "EDITARR_AUTH_LOGIN_USERNAME_ATTEMPTS": "3",
        "EDITARR_AUTH_LOGIN_ADDRESS_ATTEMPTS": "9",
        "EDITARR_AUTH_LOGIN_WINDOW_SECONDS": "120",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    config = Settings()

    assert [str(network) for network in config.auth_trusted_proxy_networks] == [
        "127.0.0.1/32",
        "10.0.0.0/8",
    ]
    assert config.auth_session_idle_seconds == 60
    assert config.auth_session_absolute_seconds == 120
    assert config.auth_remember_me_idle_seconds == 3600
    assert config.auth_remember_me_seconds == 7200
    assert config.auth_cookie_name == "editarr_dev"
    assert config.auth_cookie_samesite == "strict"
    assert config.auth_login_username_attempts == 3
    assert config.auth_login_address_attempts == 9
    assert config.auth_login_window_seconds == 120


@pytest.mark.parametrize(
    ("environment_name", "value", "message"),
    [
        ("EDITARR_AUTH_COOKIE_SAMESITE", "none", "lax"),
        ("EDITARR_AUTH_TRUSTED_PROXY_NETWORKS", '["not-a-network"]', "network"),
    ],
)
def test_invalid_authentication_environment_values_fail_actionably(
    monkeypatch, environment_name, value, message
):
    monkeypatch.setenv(environment_name, value)

    with pytest.raises(ValidationError, match=message):
        Settings()


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("auth_session_idle_seconds", 0, "must be positive"),
        ("auth_session_absolute_seconds", 0, "must be positive"),
        ("auth_remember_me_idle_seconds", 0, "must be positive"),
        ("auth_remember_me_seconds", 0, "must be positive"),
        ("auth_login_username_attempts", 0, "must be positive"),
        ("auth_login_address_attempts", 0, "must be positive"),
        ("auth_login_window_seconds", 0, "must be positive"),
    ],
)
def test_authentication_positive_values_are_required(field_name, value, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("auth_session_idle_seconds", 8 * 60 * 60 + 1, "session_idle"),
        ("auth_remember_me_idle_seconds", 30 * 24 * 60 * 60 + 1, "remember_me_idle"),
    ],
)
def test_authentication_idle_lifetimes_must_not_exceed_absolute_lifetimes(
    field_name, value, message
):
    with pytest.raises(ValidationError, match=message):
        Settings(**{field_name: value})


@pytest.mark.parametrize("cookie_name", ["", "has space", "semicolon;separator"])
def test_cookie_name_must_be_an_http_cookie_token(cookie_name):
    with pytest.raises(ValidationError, match="HTTP cookie token"):
        Settings(auth_cookie_name=cookie_name)


def test_insecure_cookie_requires_explicit_environment_escape_hatch(monkeypatch, caplog):
    with pytest.raises(ValidationError, match="EDITARR_AUTH_COOKIE_SECURE"):
        Settings(auth_cookie_secure=False)

    monkeypatch.setenv("EDITARR_AUTH_COOKIE_SECURE", "false")
    with caplog.at_level(logging.WARNING, logger="scanner.settings.config"):
        config = Settings()

    assert config.auth_cookie_secure is False
    assert "INSECURE AUTH COOKIE CONFIGURATION" in caplog.text


def test_authentication_configuration_contains_no_password_or_session_secret():
    field_names = Settings.model_fields

    assert not any("password" in name or "secret" in name for name in field_names)


def test_obsolete_public_origin_environment_variable_has_no_configuration_effect(monkeypatch):
    monkeypatch.setenv("EDITARR_AUTH_PUBLIC_ORIGIN", "not a URL")

    assert Settings().auth_cookie_secure is True
    assert "auth_public_origin" not in Settings.model_fields
