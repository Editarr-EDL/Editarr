"""Run the real API with the labelled signature-verifying community boundary double."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import uvicorn

from scanner.settings.config import settings
from tests.test_community_donations import BOUNDS

data = Path(os.environ["EDITARR_DATA_DIR"]).resolve()
scratch = data.parent
assert scratch.name.startswith("editarr-verify-")
seeded = os.environ.get("EDITARR_VERIFY_SEEDED_DATABASE")
assert not (data / "scanner.db").exists() or seeded == str(data / "scanner.db"), (
    "Community UI proof requires an empty run or the run's own legacy-members seed"
)
settings.community_preview_bounds = asdict(BOUNDS)
settings.community_preview_source_limit = 4
settings.community_publication_request_bytes = 100000

from community_double import ProbeService  # noqa: E402

from scanner import api  # noqa: E402
from scanner.http import community_identity_api  # noqa: E402
from scanner.integrations.community.transport import (  # noqa: E402
    CommunityTransport,
    ServiceEndpoint,
)

service = ProbeService()
authorized = set()
requests = []


async def boundary(request):
    """Simulate only Discord authorization and the hosted signed transport."""
    authorizations = scratch / "community-authorizations.json"
    if authorizations.exists():
        for authorization in json.loads(authorizations.read_text()):
            if authorization not in authorized:
                service.authorize(authorization)
                authorized.add(authorization)
    response = await service(request)
    requests.append({"method": request.method, "path": request.url.path})
    evidence = Path(os.environ["EDITARR_VERIFY_COMMUNITY_EVIDENCE"])
    evidence.write_text(
        json.dumps({"signatureVerified": True, "requests": requests, "posts": service.posts})
    )
    return response


community_identity_api.service_transport = lambda: CommunityTransport(
    ServiceEndpoint("https://community.test", "community.test"),
    adapter=httpx.MockTransport(boundary),
)
uvicorn.run(api.app, host="127.0.0.1", port=int(sys.argv[1]))
