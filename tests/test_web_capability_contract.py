"""The web client's capability list stays equal to the backend catalog."""

from __future__ import annotations

import re
from pathlib import Path

from scanner.security.access.capability_ids import Capability
from scanner.security.access.catalog import CAPABILITY_CONTRACT_VERSION

WEB_CAPABILITIES = Path(__file__).resolve().parents[1] / "web/src/auth/capabilities.ts"
WEB_TYPES = Path(__file__).resolve().parents[1] / "web/src/api/api-system-types.ts"


def test_the_web_capability_list_matches_the_catalog():
    source = WEB_CAPABILITIES.read_text(encoding="utf-8")
    listed = source[source.index("CAPABILITIES = [") : source.index("] as const")]

    assert re.findall(r'"([a-z_.]+)"', listed) == [capability.value for capability in Capability]


def test_the_web_payload_version_matches_the_catalog():
    assert f'version: "{CAPABILITY_CONTRACT_VERSION}"' in WEB_TYPES.read_text(encoding="utf-8")
