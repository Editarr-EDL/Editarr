"""General settings and the package shell read the same durable Dock preference."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scanner import cli
from scanner.http import macos_dock_api
from scanner.runtime import native_lifecycle
from scanner.settings import macos_dock
from tests.test_deployment_settings_api import _application, _headers


def test_only_mac_app_exposes_dock_settings_and_lifecycle_reads_saved_value(
    monkeypatch, tmp_path, capsys
):
    application, _, configuration, session, engine = _application(monkeypatch, tmp_path)
    application.include_router(macos_dock_api.router)
    monkeypatch.setattr(macos_dock_api, "settings", configuration)
    monkeypatch.setattr(macos_dock, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.delenv("EDITARR_MACOS_APP", raising=False)
    monkeypatch.setattr(cli, "get_runtime_paths", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(
        native_lifecycle,
        "NativeInstance",
        lambda _paths: SimpleNamespace(
            status=lambda: None,
        ),
    )
    path = "/editarr/api/settings/macos/dock"
    headers = _headers(configuration, session, "https://editarr.example")
    try:
        with TestClient(application) as client:
            assert client.get(path, headers=headers).json() is None
            assert client.put(path, headers=headers, json={"show_in_dock": True}).status_code == 404

            monkeypatch.setenv("EDITARR_MACOS_APP", "1")
            deployment = client.get("/editarr/api/settings/deployment", headers=headers)
            assert deployment.json()["macos_dock_supported"] is True
            assert client.get(path, headers=headers).json() == {"show_in_dock": False}
            assert client.put(path, json={"show_in_dock": True}).status_code == 401
            assert (
                client.put(path, headers=headers, json={"show_in_dock": "yes"}).status_code == 422
            )

            for enabled in (True, False):
                saved = {"show_in_dock": enabled}
                assert client.put(path, headers=headers, json=saved).json() == saved
                assert client.get(path, headers=headers).json() == saved
                assert cli._run_lifecycle(SimpleNamespace(action="status")) == 1
                assert json.loads(capsys.readouterr().out) == {
                    "running": False,
                    "instance": None,
                    **saved,
                }
    finally:
        engine.dispose()
