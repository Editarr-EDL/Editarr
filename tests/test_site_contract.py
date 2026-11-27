"""Public links, generated API reference, and website examples stay coherent."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "website/public"


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag in {"a", "link", "script", "img"}:
            link = attrs.get("href", attrs.get("src"))
            if link:
                self.links.append(link)


def test_local_links_and_anchors_exist():
    for page in PUBLIC.rglob("*.html"):
        parsed = Links(page.read_text())
        for link in parsed.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            target = PUBLIC / url.path.lstrip("/") if url.path else page
            if target.is_dir():
                target /= "index.html"
            assert target.is_file(), (page, link)
            if url.fragment:
                assert url.fragment in Links(target.read_text()).ids, (page, link)


def test_jellyfin_plugin_mentions_link_to_its_repository():
    plugin = "https://github.com/Editarr-EDL/jellyfin-edl"
    for page in PUBLIC.rglob("*.html"):
        text = " ".join(page.read_text().split())
        assert "EDL service" not in text and "jellyfin-edl service" not in text, page
        assert text.count("Jellyfin EDL plugin") == text.count(
            f'<a href="{plugin}">Jellyfin EDL plugin</a>'
        ), page


def test_published_openapi_matches_routes_and_authentication():
    from scanner.api import app

    document = app.openapi().copy()
    document.pop("servers", None)
    saved = json.loads((PUBLIC / "docs/openapi.json").read_text())
    assert saved == document, "Run scripts/export_site_openapi.py after API changes"
    assert saved["components"]["securitySchemes"]["ApiKey"]["name"] == "X-Api-Key"
    for path in ["/api/scans/history", "/api/scans", "/api/auth/api-key"]:
        for operation in saved["paths"][path].values():
            assert {"ApiKey": []} in operation["security"]


def test_every_published_operation_has_one_declared_tag():
    saved = json.loads((PUBLIC / "docs/openapi.json").read_text())
    declared = [tag["name"] for tag in saved["tags"]]
    assert len(declared) == len(set(declared))
    used = set()
    for path, operations in saved["paths"].items():
        for method, operation in operations.items():
            assert len(operation["tags"]) == 1, (method, path)
            assert operation["tags"][0] in declared, (method, path)
            used.add(operation["tags"][0])
    assert used == set(declared)
    assert "own current permissions" in saved["info"]["description"]


def test_unmatched_api_paths_fail_loudly():
    from scanner.http.openapi_tags import tag_for

    assert tag_for("/api/users/{user_id}/api-key") == "API keys"
    with pytest.raises(LookupError):
        tag_for("/api/unknown-feature")
