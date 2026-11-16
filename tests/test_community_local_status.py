import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from tests.community_identity_support import client_home


def test_local_status_rehydrates_public_identity_without_network(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    def forbidden(request):
        raise AssertionError("Local status must not contact the hosted service")

    transport = CommunityTransport(
        ServiceEndpoint("https://community.example", "community.example"),
        adapter=httpx.MockTransport(forbidden),
    )
    with Session(engine) as db:
        client = CommunityClient(db, client_home(db, tmp_path), transport)
        started = client.start()
        status = client.status()
        assert status["identity"] == started["identity"]
        assert status["identity"]["keyId"].startswith("sha256:")
        assert status["identity"]["lastKnownKeyState"] == "unenrolled"
        assert status["serviceUrl"] == "https://community.example"
        assert "authorizationUrl" not in status
        assert "secret" not in repr(status)
        assert status["pending"]["stateId"] is None
    engine.dispose()
