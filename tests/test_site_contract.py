"""Public links, generated API reference, and website examples stay coherent."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

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
