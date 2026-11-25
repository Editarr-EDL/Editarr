"""The community journey's service double honours the real service contract (w17).

Requests go through the production signed transport. Each expectation comes from
the real service: `community-service/src/http/catalog.ts` and `identity.ts`,
`src/identity/rename.ts`, and `test/enrollment-profiles.test.ts`.
"""

import asyncio
import importlib
import json
from pathlib import Path

import httpx
import pytest

from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import identity_home

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".github/skills/verify-editarr/scripts/lib"
PROFILE = json.loads((ROOT / "tests/fixtures/community/enrollment-profile-v1.json").read_text())


@pytest.fixture(scope="module")
def community_double():
    """The double's module.

    Skips when Node, esbuild, or zod cannot bundle the service validators.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(LIB))
        module = importlib.import_module("community_double")
    if reason := module.contract_unavailable():
        pytest.skip(reason)
    return module


@pytest.fixture
def double(community_double, tmp_path):
    """The double with two enrolled contributors and a transport that records each answer."""
    service = community_double.ProbeService()
    identities = []
    for name in ("member", "admin"):
        identity = load_or_create_installation_identity(identity_home(tmp_path / name))
        service.keys[identity.installation_id] = identity.public_key
        service.contributors[identity.installation_id] = f"contributor-{name}"
        identities.append(identity)
    answers = []

    async def recording(request):
        response = await service(request)
        answers.append((response.status_code, json.loads(response.content)))
        return response

    async def no_delay(_seconds):
        return None

    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(recording),
        delay=no_delay,
    )
    yield service, transport, identities, answers
    close = getattr(service, "close", None)
    if close:
        close()


def send(transport, identity, method, path, body=None, operation=None):
    """Send one signed request; return the parsed answer or the failure code."""
    content = (
        b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    )

    async def request():
        return await transport.request(
            method, path, identity=identity, operation_id=operation, body=content
        )

    try:
        return asyncio.run(request())
    except CommunityFailure as failure:
        return failure.code


def rename(transport, identity, name, operation):
    return send(transport, identity, "PATCH", "/v1/identity", {"displayName": name}, operation)


def test_contributors_start_with_distinct_valid_names(double):
    service, transport, (member, admin), _ = double
    profiles = [send(transport, who, "GET", "/v1/identity") for who in (member, admin)]
    assert all(set(PROFILE["profileRequiredFields"]) <= set(profile) for profile in profiles)
    names = [profile["displayName"] for profile in profiles]
    assert names[0].lower() != names[1].lower()
    bounds = range(PROFILE["nameMinCodePoints"], PROFILE["nameMaxCodePoints"] + 1)
    assert all(len(name) in bounds for name in names)


def test_identity_accepts_only_its_real_methods(double):
    _, transport, (member, _), answers = double
    send(transport, member, "DELETE", "/v1/identity", operation="delete-identity")
    assert answers[-1] == (405, {"error": {"code": "method_not_allowed"}})


def test_rename_body_must_hold_only_a_valid_display_name(double):
    _, transport, (member, _), answers = double
    send(transport, member, "PATCH", "/v1/identity", {"displayName": "ok", "x": 1}, "extra-key")
    assert answers[-1] == (400, {"error": {"code": "invalid_display_name"}})
    rename(transport, member, "no", "too-short")
    assert answers[-1] == (400, {"error": {"code": "invalid_display_name"}})


def test_rename_operation_replays_the_same_body_and_refuses_a_different_one(double):
    _, transport, (member, _), answers = double
    assert rename(transport, member, "First Replay Name", "same-operation")["displayName"] == (
        "First Replay Name"
    )
    revision = answers[-1][1]["profileRevision"]
    replay = rename(transport, member, "First Replay Name", "same-operation")
    assert (replay["displayName"], replay["profileRevision"]) == ("First Replay Name", revision)
    rename(transport, member, "Conflicting Replay Name", "same-operation")
    assert answers[-1] == (409, {"error": {"code": "operation_conflict"}})
    current = send(transport, member, "GET", "/v1/identity")
    assert current["displayName"] == "First Replay Name"


@pytest.mark.parametrize("fold", [str, str.upper, str.lower])
def test_rename_refuses_another_contributors_current_name_in_any_case(double, fold):
    _, transport, (member, admin), answers = double
    original = send(transport, member, "GET", "/v1/identity")["displayName"]
    taken = send(transport, admin, "GET", "/v1/identity")["displayName"]
    rename(transport, member, fold(taken), "take-admin-name")
    assert answers[-1] == (409, {"error": {"code": "display_name_taken"}})
    assert send(transport, member, "GET", "/v1/identity")["displayName"] == original
    rename(transport, member, "Probe Member Name", "own-rename")
    restored = rename(transport, member, original, "restore-own-name")
    assert restored["displayName"] == original
