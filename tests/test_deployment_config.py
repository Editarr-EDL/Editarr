import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from scanner.settings.deployment_config import (
    CookieScope,
    DeploymentConfigurationError,
    DeploymentConfigurationUnavailable,
    DeploymentStore,
    parse_base_url,
    resolve_deployment_snapshot,
)


@pytest.mark.parametrize(
    ("value", "base_url", "origin", "prefix"),
    [
        ("https://EDITARR.example:443", "https://editarr.example", "https://editarr.example", "/"),
        ("https://editarr.example/", "https://editarr.example", "https://editarr.example", "/"),
        (
            "https://editarr.example/Media_2/editarr-1.0~/",
            "https://editarr.example/Media_2/editarr-1.0~",
            "https://editarr.example",
            "/Media_2/editarr-1.0~",
        ),
        (
            "https://editarr.example/editarr/",
            "https://editarr.example/editarr",
            "https://editarr.example",
            "/editarr",
        ),
        (
            "https://editarr.example/media/editarr/",
            "https://editarr.example/media/editarr",
            "https://editarr.example",
            "/media/editarr",
        ),
        (
            "http://[2001:db8::1]:8081/a/",
            "http://[2001:db8::1]:8081/a",
            "http://[2001:db8::1]:8081",
            "/a",
        ),
    ],
)
def test_parse_base_url_canonicalizes_origin_and_prefix(value, base_url, origin, prefix):
    parsed = parse_base_url(value)

    assert parsed.base_url == base_url
    assert parsed.public_origin.serialized == origin
    assert parsed.prefix == prefix


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ftp://editarr.example",
        "https://user@editarr.example",
        "https://editarr.example/?query=value",
        "https://editarr.example/#fragment",
        "https://editarr.example/?",
        "https://editarr.example/#",
        "https://editarr.example/caf%C3%A9",
        "https://editarr.example/%65ditarr",
        "https://editarr.example/café",
        "https://editarr.example/a&b",
        "https://editarr.example/a;b",
        "https://editarr.example/a<b>",
        "https://editarr.example/%252f",
        "https://editarr.example/a/./b",
        "https://editarr.example/a/../b",
        "https://editarr.example/a/%2f/b",
        "https://editarr.example/a/%5C/b",
        "https://editarr.example/a//b",
        "https://editarr.example\\evil",
        "https://editarr.example/a\x00b",
        "https://editarr.example:70000",
    ],
)
def test_parse_base_url_rejects_ambiguous_or_unsafe_values(value):
    with pytest.raises(DeploymentConfigurationError):
        parse_base_url(value)


def test_local_default_is_unsaved_and_follows_saved_port(tmp_path):
    store = DeploymentStore(tmp_path)
    initial = resolve_deployment_snapshot(store=store, environment={})

    assert initial.base_url == "http://localhost:3348"
    assert initial.prefix == "/"
    assert initial.base_url_source == "local_default"
    assert initial.port_source == "default"
    assert initial.cookie_secure is False
    assert not store.path.exists()

    store.update(port=9123)
    after_port_save = resolve_deployment_snapshot(store=store, environment={})

    assert after_port_save.base_url == "http://localhost:9123"
    assert after_port_save.base_url_source == "local_default"
    assert after_port_save.port_source == "saved"
    assert DeploymentStore(tmp_path).read().base_url is None


def test_explicit_empty_environment_ignores_process_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_BASE_URL", "https://process.example/editarr")

    snapshot = resolve_deployment_snapshot(store=DeploymentStore(tmp_path), environment={})

    assert snapshot.base_url == "http://localhost:3348"
    assert snapshot.base_url_source == "local_default"


def test_saved_base_url_overrides_changed_environment_and_public_port_is_not_listener(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="https://example.test:9443/editarr/", port=8001)

    snapshot = resolve_deployment_snapshot(
        store=DeploymentStore(tmp_path),
        environment={"EDITARR_BASE_URL": "https://other.test/prefix"},
    )

    assert snapshot.base_url == "https://example.test:9443/editarr"
    assert snapshot.public_origin.serialized == "https://example.test:9443"
    assert snapshot.prefix == "/editarr"
    assert snapshot.listening_port == 8001
    assert snapshot.base_url_source == "saved"


def test_explicit_root_seed_save_and_clear_remain_distinct(tmp_path):
    store = DeploymentStore(tmp_path)
    environment = {"EDITARR_BASE_URL": "https://example.test/editarr"}

    seed = resolve_deployment_snapshot(store=store, environment=environment)
    assert seed.base_url_source == "environment"
    store.update(port=8002)
    assert (
        resolve_deployment_snapshot(store=store, environment=environment).base_url_source
        == "environment"
    )

    store.update(base_url="https://example.test/")
    explicit_root = resolve_deployment_snapshot(
        store=DeploymentStore(tmp_path), environment=environment
    )
    assert explicit_root.base_url == "https://example.test"
    assert explicit_root.base_url_source == "saved"

    store.update(base_url=None)
    assert (
        resolve_deployment_snapshot(store=DeploymentStore(tmp_path), environment=environment).prefix
        == "/editarr"
    )


def test_cli_port_has_temporary_precedence_without_persisting(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(port=9001)

    snapshot = resolve_deployment_snapshot(store=store, cli_port_override=9002, environment={})

    assert snapshot.listening_port == 9002
    assert snapshot.port_source == "cli"
    assert snapshot.cli_port_override == 9002
    assert DeploymentStore(tmp_path).read().port == 9001


def test_invalid_lower_priority_environment_and_obsolete_origin_do_not_override_saved_url(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="https://saved.example/app")

    snapshot = resolve_deployment_snapshot(
        store=store,
        environment={
            "EDITARR_BASE_URL": "not a URL",
            "EDITARR_AUTH_PUBLIC_ORIGIN": "not a URL either",
        },
    )

    assert snapshot.base_url == "https://saved.example/app"
    assert snapshot.base_url_source == "saved"


def test_invalid_selected_environment_or_non_loopback_local_mode_is_actionable(tmp_path):
    store = DeploymentStore(tmp_path)

    with pytest.raises(DeploymentConfigurationError, match="Base URL"):
        resolve_deployment_snapshot(store=store, environment={"EDITARR_BASE_URL": "invalid"})
    with pytest.raises(DeploymentConfigurationError, match="required"):
        resolve_deployment_snapshot(store=store, environment={}, bind_host="192.0.2.1")


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_direct_access_all_interface_binding_needs_no_fixed_base_url(tmp_path, host):
    snapshot = resolve_deployment_snapshot(
        store=DeploymentStore(tmp_path), environment={}, bind_host=host
    )
    assert snapshot.base_url_source == "local_default"
    assert snapshot.cookie_secure is False
    assert not (tmp_path / "deployment.json").exists()


def test_http_and_local_cookie_security_require_the_approved_policy(tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="http://editarr.test")
    with pytest.raises(DeploymentConfigurationError, match="COOKIE_SECURE=false"):
        resolve_deployment_snapshot(store=store, environment={})
    insecure = resolve_deployment_snapshot(
        store=store,
        environment={},
        auth_cookie_secure=False,
        auth_cookie_secure_overridden=True,
    )
    assert insecure.cookie_secure is False

    local_store = DeploymentStore(tmp_path / "local")
    with pytest.raises(DeploymentConfigurationError, match="conflicts"):
        resolve_deployment_snapshot(
            store=local_store,
            environment={},
            auth_cookie_secure=True,
            auth_cookie_secure_overridden=True,
        )


def test_corrupt_file_is_reported_without_resetting_it(tmp_path):
    store = DeploymentStore(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    store.path.write_text('{"version": 999}', encoding="utf-8")

    with pytest.raises(DeploymentConfigurationError, match="unsupported version"):
        store.read()

    assert store.path.read_text(encoding="utf-8") == '{"version": 999}'


def test_failed_atomic_write_preserves_previous_configuration(monkeypatch, tmp_path):
    store = DeploymentStore(tmp_path)
    store.update(base_url="https://before.example")
    original = store.path.read_text(encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("scanner.settings.deployment_config.os.replace", fail_replace)
    with pytest.raises(DeploymentConfigurationUnavailable, match="Unable to write"):
        store.update(port=9000)

    assert store.path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".deployment-*.tmp"))


def test_cookie_scope_cutoff_is_persisted_and_cannot_move_backward(tmp_path):
    store = DeploymentStore(tmp_path)
    cutoff = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    store.update(
        cookie_scope=CookieScope(
            not_before=cutoff,
            legacy_paths=("/", "/editarr", "/editarr/"),
            pending_previous_path="/old/",
        )
    )

    reloaded = DeploymentStore(tmp_path).read().cookie_scope
    assert reloaded == CookieScope(
        not_before=cutoff,
        legacy_paths=("/", "/editarr"),
        pending_previous_path="/old",
    )
    with pytest.raises(DeploymentConfigurationError, match="must not move backward"):
        store.update(cookie_scope=CookieScope(not_before=cutoff - timedelta(seconds=1)))


def test_concurrent_partial_updates_keep_both_fields(tmp_path):
    store = DeploymentStore(tmp_path)
    start = threading.Barrier(3)
    failures: list[BaseException] = []

    def update(**kwargs):
        try:
            start.wait()
            DeploymentStore(tmp_path).update(**kwargs)
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    first = threading.Thread(target=update, kwargs={"base_url": "https://saved.example/app"})
    second = threading.Thread(target=update, kwargs={"port": 9010})
    first.start()
    second.start()
    start.wait()
    first.join()
    second.join()

    assert failures == []
    reloaded = DeploymentStore(tmp_path).read()
    assert reloaded.base_url == "https://saved.example/app"
    assert reloaded.port == 9010
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "base_url": "https://saved.example/app",
        "port": 9010,
        "version": 1,
    }
